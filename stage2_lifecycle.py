"""Stage-2 lifecycle evidence and explicit future volume preparation.

Importing/validating is offline. Only prepare-volume invokes Docker, explicitly.
No deletion, startup, restart, image pull, or historical-volume write exists here.
"""
from datetime import datetime, timezone
import json
import re
import subprocess
import time
import uuid

from stage2_contract import ROOT, PLAN_SHA256, require, sha256, load_plan
from stage2_readiness import repository_evidence, load_spec

DEFAULT_VOLUME = 'ccnc-mqtt-expiry_broker_data'
P2_IDS = {'mqtt_expiry_v3_physical_v3_predictive_admission',
          'mqtt_expiry_v3_predictive_physical_worker'}
P2_TOPIC = 'ccnc/expiry/v3/internal/predictive_accepted'
DEPLOYMENT_FILES = ['compose.stage2.yaml', 'ha/stage2/expiry_physical_v3.yaml',
                    'stage2_lifecycle.py', 'stage2_runtime.py', 'stage2_backend.py', 'stage2_runner.py',
                    'stage2_dependencies.py', 'requirements.txt', 'stage2_preflight_pinned.ps1']
ISOLATION = ('Experiment owns the isolated loopback broker; no unmodeled publisher '
             'is authorized to publish commands or the internal P2 topic during Stage 2.')


def now_ms():
    return time.time_ns()/1e6


def utc_ms(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()*1000


def valid_volume(name):
    require(isinstance(name, str) and re.fullmatch(r'ccnc-mqtt-expiry_stage2_[0-9a-f]{32}', name),
            'Unexpected/default Stage-2 volume')


def source_evidence(expected_commit, *, committed=True):
    plan = load_plan(expected_sha256=PLAN_SHA256)
    repo = repository_evidence(plan)
    require(repo['commit'] == expected_commit, 'Source commit changed')
    for name in DEPLOYMENT_FILES:
        repo['source_sha256'][name] = sha256(ROOT/name)
    for path in (ROOT/'ha/packages').glob('*.yaml'):
        repo['source_sha256'][path.relative_to(ROOT).as_posix()] = sha256(path)
    if committed:
        for name in repo['source_sha256']:
            data = subprocess.check_output(['git', 'show', expected_commit+':'+name], cwd=ROOT)
            require(data.replace(b'\r\n', b'\n') == (ROOT/name).read_bytes().replace(b'\r\n', b'\n'),
                    'Commit reviewed source before deployment: '+name)
    # The overlay may add startup state only. Scientific actions remain frozen.
    import yaml
    base = yaml.safe_load((ROOT/'ha/packages/expiry_physical_v3.yaml').read_text(encoding='utf-8'))
    overlay = yaml.safe_load((ROOT/'ha/stage2/expiry_physical_v3.yaml').read_text(encoding='utf-8'))
    for automation in overlay['automation']:
        require(automation.pop('initial_state', None) is (automation['id'] not in P2_IDS),
                'Wrong deployment startup state')
    require(base == overlay, 'Unexplained Stage-2 deployment semantics change')
    return repo


def docker(*args):
    result = subprocess.run(['docker', *args], cwd=ROOT, capture_output=True, text=True, timeout=60)
    require(result.returncode == 0, 'Docker command failed: '+str(args[:3]))
    return result.stdout.strip()


def prepare_volume(path, expected_commit, command=docker):
    """Future DEPLOYMENT operation, not read-only preflight. No stack startup."""
    repo = source_evidence(expected_commit)
    spec = load_spec()
    name = 'ccnc-mqtt-expiry_stage2_'+uuid.uuid4().hex
    record = {'schema': 'STAGE2-VOLUME-1', 'volume_name': name,
              'default_volume': DEFAULT_VOLUME, 'plan_sha256': PLAN_SHA256,
              'repository': repo, 'isolation_assumption': ISOLATION,
              'candidate_experiment': False, 'prepared': False}
    # Reserve the artifact before any volume mutation; preserve failure evidence.
    with open(path, 'x', encoding='utf-8') as stream:
        try:
            cached = json.loads(command('image', 'inspect', 'eclipse-mosquitto:2.0.22'))[0]
            expected = spec['fields']['images']['historical_value']['broker']
            require({k: cached[k] for k in ('Id', 'RepoDigests')} == expected, 'Cached broker image mismatch')
            names = command('volume', 'ls', '--format', '{{.Name}}').splitlines()
            require(name not in names, 'Stage-2 volume already exists')
            record['absence_observed_at_ms'] = now_ms()
            record['volume_names_before_creation'] = names
            require(command('volume', 'create', '--label', 'ccnc.stage2='+name, name) == name,
                    'Unexpected created volume')
            volume = json.loads(command('volume', 'inspect', name))[0]
            require(volume['Name'] == name and volume['Labels'].get('ccnc.stage2') == name,
                    'Volume identity/label mismatch')
            require(volume.get('Driver') == 'local' and not volume.get('Options'), 'Unexpected storage driver/options')
            record['volume_inspect'] = volume
            # Bypass image entrypoint; read-only mount and nocopy. Broker never runs.
            output = command('run', '--rm', '--pull', 'never', '--network', 'none',
                             '--read-only', '--mount', 'type=volume,source='+name+',target=/state,readonly,volume-nocopy',
                             '--entrypoint', '/bin/sh', expected['Id'], '-c',
                             'find /state -mindepth 1 -print')
            require(output == '', 'New volume contains pre-existing entries')
            record.update(empty_listing=output, empty_verified_at_ms=now_ms(),
                          empty_check_image=expected, prepared=True)
        except BaseException as exc:
            record['failure_reason'] = str(exc)
            raise
        finally:
            json.dump(record, stream, indent=2)
            stream.write('\n')
    return record


def validate_lifecycle(prepared, current, expected_volume, expected_commit):
    """Validate collected evidence, never infer lifecycle from a quiet interval."""
    valid_volume(expected_volume)
    require(prepared['schema'] == 'STAGE2-VOLUME-1' and prepared['prepared'] is True, 'Missing volume preparation')
    require(prepared['volume_name'] == expected_volume and prepared['default_volume'] == DEFAULT_VOLUME,
            'Volume identity mismatch')
    require(expected_volume not in prepared['volume_names_before_creation'], 'Volume was not new')
    require(prepared['empty_listing'] == '', 'Unsafe pre-start persistence state')
    require(prepared['empty_check_image'] == load_spec()['fields']['images']['historical_value']['broker'],
            'Unreviewed empty-check image')
    require(prepared['plan_sha256'] == PLAN_SHA256 and prepared['repository']['commit'] == expected_commit,
            'Lifecycle plan/commit mismatch')
    require(current['repository'] == prepared['repository'], 'Source changed after volume preparation')
    require(prepared['isolation_assumption'] == ISOLATION, 'Missing isolation assumption')
    require(prepared['volume_inspect'] == current['volume_inspect'], 'Volume changed/recreated')
    require(prepared['volume_inspect']['Name'] == expected_volume, 'Unexpected inspected volume')
    require(prepared['volume_inspect'].get('Driver') == 'local'
            and not prepared['volume_inspect'].get('Options'), 'Unexpected persistent storage options')
    require(prepared['absence_observed_at_ms'] <= utc_ms(prepared['volume_inspect']['CreatedAt']) + 1000
            <= prepared['empty_verified_at_ms'] + 1000, 'Volume creation ordering invalid')
    broker, ha = current['containers']['broker'], current['containers']['homeassistant']
    for container in (broker, ha):
        require(container['running'] is True and container['restart_count'] == 0, 'Container stopped/restarted')
        require(utc_ms(container['created_at']) > prepared['empty_verified_at_ms'],
                'Container predates empty-volume check; recreate both services')
        require(utc_ms(container['started_at']) >= utc_ms(container['created_at']), 'Container lifecycle timestamps')
    mounts = [m for m in broker['mounts'] if m['Destination'] == '/mosquitto/data']
    require(len(mounts) == 1 and mounts[0]['Type'] == 'volume'
            and mounts[0]['Name'] == expected_volume, 'Historical/unexpected broker data mount')
    require(not any(m.get('Name') == DEFAULT_VOLUME for m in broker['mounts']), 'Historical volume mounted')
    epoch = current['selected_epoch']
    require(epoch and epoch['protocol'] == 5 and epoch['connected'], 'Missing active MQTT5 epoch')
    require(epoch['connect']['timestamp']*1000 >= utc_ms(broker['started_at'])-1000,
            'HA epoch predates broker lifecycle')
    require(epoch['connect']['timestamp']*1000 >= utc_ms(ha['started_at'])-1000,
            'HA epoch predates HA lifecycle')
    return {'volume_name': expected_volume, 'broker_id': broker['container_id'],
            'broker_started_at': broker['started_at'], 'ha_id': ha['container_id'],
            'ha_started_at': ha['started_at'], 'ha_epoch': epoch,
            'default_volume_mounted': False, 'isolation_assumption': ISOLATION}


def require_ready(ready, lifecycle, wall_ms, monotonic_ns):
    require(ready and ready['lifecycle'] == lifecycle, 'Missing/stale observer lifecycle readiness')
    require(wall_ms > ready['wall_ms'] and monotonic_ns > ready['monotonic_ns'],
            'Candidate must follow observer readiness')
