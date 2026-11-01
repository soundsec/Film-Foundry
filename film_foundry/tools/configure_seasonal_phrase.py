"""Replace the hidden collection phrase without changing identities or unlocks.

Run as a module from the source tree, then sync the shared Android resources
with android/tools/sync_seasonal_collection.py. Existing entitlements are kept.
"""
from getpass import getpass
import copy
from film_foundry.app.seasonal_collection import seal_phrase
from film_foundry.app.finder_resources import payload, pack, resource_path


def main():
    phrase = getpass("New collection phrase: ")
    if phrase != getpass("Repeat phrase: "):
        raise SystemExit("Phrases do not match; nothing changed.")
    data = copy.deepcopy(payload())
    data["collection"]["sealed_phrase"] = seal_phrase(phrase)
    path=resource_path();temporary=path.with_suffix(".pending")
    temporary.write_bytes(pack(data));temporary.replace(path)
    print("Phrase updated. Installation IDs and existing unlocks are unchanged.")


if __name__ == "__main__":
    main()
