"""Local operator handoff. Outputs a token or a sanitized error, never a path."""
from __future__ import annotations

import argparse
import json
import os

from bridge.config import ConfigError, load_config
from bridge.errors import BridgeError
from bridge.repository import Repository, StorageError, open_repository
from bridge.staging import gc_expired_staging, stage_image


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid arguments")


def main() -> int:
    parser = _Parser(description="Stage one explicitly selected image under the configured input root.")
    parser.add_argument("--relative-path")
    parser.add_argument("--agent-id")
    parser.add_argument("--project-id", default="")
    parser.add_argument("--channel", default="")
    parser.add_argument("--source-instance-id", default="")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--gc", action="store_true", help="Delete tracked expired staging files only.")
    repo = None
    try:
        args = parser.parse_args()
        config = load_config(dict(os.environ))
        repo = Repository(open_repository(config.data_dir))
        if args.gc:
            if args.relative_path or args.agent_id:
                raise ValueError("invalid arguments")
            result = gc_expired_staging(config=config, repo=repo, confirmed=args.confirm)
            print(json.dumps({"ok": True, **result}))
            return 0
        if args.relative_path is None or args.agent_id is None:
            raise ValueError("invalid arguments")
        token = stage_image(config=config, repo=repo,
            scope=(args.source_instance_id, args.agent_id, args.project_id, args.channel),
            relative_path=args.relative_path, confirmed=args.confirm)
        print(json.dumps({"ok": True, "staging_id": token}))
        return 0
    except BridgeError as exc:
        print(json.dumps({"ok": False, "error": {"code": exc.code}}))
    except (ConfigError, StorageError, ValueError, OSError):
        print(json.dumps({"ok": False, "error": {"code": "invalid_request"}}))
    except Exception:
        print(json.dumps({"ok": False, "error": {"code": "storage_error"}}))
    finally:
        if repo is not None:
            repo.close()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
