"""Check that object storage accepts the uploader's writes and never replaces an object.

Run it once with the uploader's settings before the uploader first runs. It
writes, reads and deletes one object under probe/, and touches nothing else.
It exits 1 when a check fails.
"""

import sys

from lamp_collector.settings import UploaderSettings
from lamp_storage import ObjectConflictError, S3Store, s3_client

PROBE_KEY = "probe/create-only-check"


def main() -> int:
    store = open_store(UploaderSettings())
    store.delete(PROBE_KEY)
    results = run_checks(store)
    store.delete(PROBE_KEY)
    for name, passed in results.items():
        sys.stdout.write(f"{'ok  ' if passed else 'FAIL'} {name}\n")
    return 0 if all(results.values()) else 1


def open_store(settings: UploaderSettings) -> S3Store:
    client = s3_client(
        settings.lamp_s3_endpoint_url, settings.credentials(), settings.aws_default_region
    )
    return S3Store(client, settings.lamp_private_bucket)


def run_checks(store: S3Store) -> dict[str, bool]:
    results = {"a new object is stored": store.put_new(PROBE_KEY, b"first")}
    results["the same object again is skipped"] = not store.put_new(PROBE_KEY, b"first")
    results["a different object is refused"] = refuses_a_different_object(store)
    results["the first object is kept"] = store.get(PROBE_KEY) == b"first"
    return results


def refuses_a_different_object(store: S3Store) -> bool:
    try:
        store.put_new(PROBE_KEY, b"second")
    except ObjectConflictError:
        return True
    return False


if __name__ == "__main__":
    sys.exit(main())
