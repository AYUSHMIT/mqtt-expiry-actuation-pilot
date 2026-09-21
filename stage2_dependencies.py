"""Offline runtime-pin inspection. Importing this module never opens a transport."""
import argparse
import importlib
from importlib import metadata
import json
from pathlib import Path
import sys

PINS = {'paho-mqtt': ('2.1.0', 'paho.mqtt.client'),
        'websocket-client': ('1.9.0', 'websocket'), 'PyYAML': ('6.0.2', 'yaml')}


def inspect_dependencies():
    packages = {}
    for name, (expected, module) in PINS.items():
        item = dict(expected_version=expected, actual_version=None, module_path=None, passed=False)
        try:
            item['actual_version'] = metadata.version(name)
            item['module_path'] = str(Path(importlib.import_module(module).__file__).resolve())
            item['passed'] = item['actual_version'] == expected
        except (ImportError, metadata.PackageNotFoundError, AttributeError, TypeError):
            item['failure'] = 'Missing distribution or importable module'
        packages[name] = item
    return dict(python_executable=sys.executable, python_prefix=sys.prefix,
                packages=packages, passed=all(p['passed'] for p in packages.values()))


def require_dependencies():
    report = inspect_dependencies()
    if not report['passed']:
        raise ValueError('Pinned experiment dependencies required: '+json.dumps(report, sort_keys=True))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    report = inspect_dependencies()
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
