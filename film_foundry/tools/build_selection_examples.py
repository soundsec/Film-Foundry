"""Build read-only comparison thumbnails for the bundled public presets."""
from film_foundry.app import workflow
from film_foundry.app.control import WorkflowControl
from film_foundry.app.material_selection import VARIANTS, material_choices
from film_foundry.app.seasonal_collection import collection_ids
from film_foundry.app.selection_preview import SelectionPreviewSession
from film_foundry.app.thumbnail_cache import FACTORY_THUMBNAILS, ThumbnailCache
from film_foundry.core.atomic_io import atomic_output_path


class BundledPreview(SelectionPreviewSession):
    @staticmethod
    def preset_path(bank, key):
        return workflow.PRESET_DIR / bank / (key + '.json')


def build():
    with atomic_output_path(FACTORY_THUMBNAILS) as temporary:
        cache = ThumbnailCache(temporary)
        control = WorkflowControl()
        session = BundledPreview(cache=cache)
        try:
            for family in VARIANTS:
                for record in material_choices(family):
                    session.render(record, False, control)
            base = session.film_config({'key': 'clear_modern_negative'})
            for bank in ('film', 'develop', 'scanner'):
                session = BundledPreview(bank=bank, base=base, cache=cache)
                names = workflow.public_builtin_preset_names(workflow.PRESET_DIR / bank)
                if bank in ('film','scanner'):
                    names -= collection_ids(bank)
                for key in sorted(names):
                    session.render({'key':key}, False, control)
            count = cache.statistics()['entries']
            with cache.connect() as db:
                db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchall()
                db.execute('PRAGMA journal_mode=DELETE')
        finally:
            cache.close_thread_connection()
    return count


if __name__ == '__main__':
    print(f'{build()} factory previews prepared')
