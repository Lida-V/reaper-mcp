"""Write machine-specific configuration; never overwrite an existing setup."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reaper', type=Path, required=True, help='Full path to reaper.exe')
    parser.add_argument('--plugin-cache-source', type=Path, required=True,
                        help='Initialized REAPER resource directory: reuse three FX index caches without scanning')
    args = parser.parse_args()
    executable = args.reaper.expanduser().resolve()
    if not executable.is_file():
        parser.error('REAPER executable not found')
    target = {'executable': str(executable)}
    if args.plugin_cache_source:
        source = args.plugin_cache_source.expanduser().resolve()
        if not source.is_dir():
            parser.error('Plugin cache directory not found')
        cache = source / 'reaper-vstplugins64.ini'
        if not cache.is_file() or not any(b'=' in line for line in cache.read_bytes().splitlines()):
            parser.error('Prepared REAPER VST index not found; automatic system scans are not performed')
        target['pluginCacheSource'] = str(source)
    directory = ROOT / 'config'
    directory.mkdir(exist_ok=True)
    path = directory / 'local.json'
    try:
        with path.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump({'target': target}, stream, indent=2, ensure_ascii=False)
            stream.write('\n')
    except FileExistsError:
        parser.error('config/local.json already exists; edit it explicitly to change the setup')
    print('Created ignored config/local.json. Run python scripts/reaper_api.py start next.')

if __name__ == '__main__':
    main()
