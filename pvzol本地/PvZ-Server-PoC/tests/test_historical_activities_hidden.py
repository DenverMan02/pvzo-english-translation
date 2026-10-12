"""Regression check: unsupported archived promotions must not enter the login list."""
import os
import sys
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import historical_activities as activities  # noqa: E402


class ClientDB:
    def _read_clientdb_table(self, _table_name):
        return [["id"]] + [[str(icon_id)] for icon_id in activities.HISTORIC_VIEW_TYPES]


def main():
    activities._ICON_ROWS = None
    activities._ARCHIVED_IDS = None
    assert activities.common_entries(ClientDB()) == b""
    assert activities.VISIBLE_ARCHIVED_ICON_IDS == frozenset()

    # The previously exported drop-in package must not re-enable the same unsafe list.
    export_path = (os.path.dirname(HERE) + os.sep + "patch_exports" + os.sep
                   + "old-limited-activities" + os.sep + "old_limited_activities.py")
    spec = importlib.util.spec_from_file_location("old_limited_activities_export", export_path)
    exported = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exported)
    exported._ICON_ROWS = None
    exported._ARCHIVED_IDS = None
    assert exported.common_entries(ClientDB()) == b""
    assert exported.VISIBLE_ARCHIVED_ICON_IDS == frozenset()
    print("historical activities: unsupported archived promos stay hidden")


if __name__ == "__main__":
    main()
