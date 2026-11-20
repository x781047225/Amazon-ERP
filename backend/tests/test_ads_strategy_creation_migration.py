import importlib.util
from pathlib import Path
from alembic.operations import Operations
from alembic.migration import MigrationContext
from app.models import now
from app.db import Session,engine
from app.ads_strategy_models import AdsStrategy

def test_creation_time_migration_preserves_legacy_rows():
    with Session.begin() as db:
        db.add(AdsStrategy(id='legacy',owner='creator',name='old',country='UK',kind='state',config={}))
    path=Path(__file__).parents[1]/'migrations/versions/0055_ads_strategy_created_at.py'
    spec=importlib.util.spec_from_file_location('creation_migration',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            module.downgrade();module.upgrade()
    with Session() as db:
        old=db.get(AdsStrategy,'legacy');assert old.owner=='creator' and old.name=='old' and old.created_at is None
    with Session.begin() as db:
        db.add(AdsStrategy(id='new',owner='creator',name='new',country='UK',kind='state',config={}))
    with Session() as db:assert db.get(AdsStrategy,'new').created_at is not None
