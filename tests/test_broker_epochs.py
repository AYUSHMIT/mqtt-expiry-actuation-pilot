"""Synthetic Mosquitto lifecycle logs only; no Docker or network access."""
import unittest

from boundary_v3 import (parse_active_mqtt_clients, require_active_mqtt_client,
                         BrokerEvidenceError, REQUIRED_MQTT_TOPICS)


def connect(cid, protocol=5):
    return f'New client connected from 127.0.0.1:1234 as {cid} (p{protocol}, c1, k60).'


def subscriptions(cid, topics=REQUIRED_MQTT_TOPICS):
    return [f'{cid} 0 {topic}' for topic in sorted(topics)]


def complete(cid, protocol=5):
    return [connect(cid, protocol), *subscriptions(cid)]


def parse(lines):
    return parse_active_mqtt_clients('\n'.join(
        f'broker-1 | {1000+i}: {line}' for i, line in enumerate(lines)), REQUIRED_MQTT_TOPICS)


class BrokerEpochTests(unittest.TestCase):
    def assert_candidates(self, lines, expected):
        evidence = parse(lines)
        self.assertEqual(evidence['active_complete_candidates'], expected)
        self.assertEqual(evidence['active_complete_candidate_count'], len(expected))
        if len(expected) == 1:
            self.assertEqual(require_active_mqtt_client(evidence), expected[0])
        else:
            with self.assertRaises(BrokerEvidenceError) as raised:
                require_active_mqtt_client(evidence)
            self.assertEqual(raised.exception.broker_evidence, evidence)
        return evidence

    def test_single_complete_active_epoch(self):
        result = self.assert_candidates(complete('active'), ['active'])
        epoch = result['epochs'][0]
        self.assertEqual(epoch['connect'], dict(line=1, timestamp=1000))
        self.assertEqual(epoch['protocol'], 5)
        self.assertEqual(epoch['subscriptions'], sorted(REQUIRED_MQTT_TOPICS))

    def test_historical_close_then_complete_replacement(self):
        result = self.assert_candidates(complete('old') + ['Client old closed its connection.']
                                       + complete('new'), ['new'])
        self.assertFalse(result['epochs'][0]['connected'])
        self.assertEqual(result['epochs'][0]['end_reason'], 'closed its connection.')
        self.assertIn('end', result['epochs'][0])

    def test_historical_complete_replacement_incomplete_fails(self):
        self.assert_candidates(complete('old') + ['Client old closed its connection.', connect('new')]
                               + subscriptions('new', [sorted(REQUIRED_MQTT_TOPICS)[0]]), [])

    def test_two_active_complete_clients_fail(self):
        self.assert_candidates(complete('a') + complete('b'), ['a', 'b'])

    def test_mqtt4_cannot_qualify(self):
        self.assert_candidates(complete('v4', 4), [])

    def test_same_id_reconnect_must_earn_every_subscription(self):
        for close in ([], ['Client reused closed its connection.']):
            with self.subTest(close=close):
                lines = complete('reused') + close + [connect('reused')]
                result = self.assert_candidates(lines, [])
                self.assertEqual(result['epochs'][-1]['subscriptions'], [])
                self.assertFalse(result['epochs'][0]['connected'])
                self.assert_candidates(lines + subscriptions('reused'), ['reused'])

    def test_disconnect_before_complete(self):
        self.assert_candidates([connect('a'), subscriptions('a')[0], 'Client a disconnected.'], [])

    def test_noncurrent_subscriptions_do_not_resurrect(self):
        result = self.assert_candidates([connect('a'), 'Client a closed its connection.']
                                       + subscriptions('a') + subscriptions('unknown'), [])
        self.assertEqual(result['ignored_noncurrent_subscriptions'], 2*len(REQUIRED_MQTT_TOPICS))

    def test_unrelated_history_does_not_affect_selection(self):
        self.assert_candidates([connect('other'), 'other 1 unrelated/topic', 'Client other disconnected.']
                               + complete('ha'), ['ha'])

    def test_actual_cid_qos_topic_format_and_all_qos_values(self):
        topics = sorted(REQUIRED_MQTT_TOPICS)
        self.assert_candidates([connect('ha')] + [f'\tha {i%3} {t}' for i,t in enumerate(topics)], ['ha'])

    def test_close_removes_only_named_client(self):
        for wording in ('closed its connection.', 'disconnected.'):
            self.assert_candidates(complete('a') + complete('b') + [f'Client a {wording}'], ['b'])

    def test_unknown_malformed_lines_and_pings_do_not_create_clients(self):
        self.assert_candidates(['New client connected as ghost (p5).',
                                'Client ghost connected.', 'Received PINGREQ from ghost',
                                'Sending PINGRESP to ghost'] + subscriptions('ghost'), [])

    def test_missing_any_required_topic_fails(self):
        for missing in REQUIRED_MQTT_TOPICS:
            with self.subTest(missing=missing):
                self.assert_candidates([connect('ha')] + subscriptions('ha', REQUIRED_MQTT_TOPICS-{missing}), [])

    def test_same_timestamp_preserves_log_order(self):
        lines = complete('a') + ['Client a closed its connection.']
        result = parse_active_mqtt_clients('\n'.join('1000: '+line for line in lines), REQUIRED_MQTT_TOPICS)
        self.assertEqual(result['active_complete_candidate_count'], 0)


if __name__ == '__main__':
    unittest.main()
