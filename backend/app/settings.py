import json, os
from pathlib import Path
from urllib.parse import quote
ROOT = Path(os.environ.get('ERP_ROOT', Path(__file__).resolve().parents[2]))
LOCAL = json.loads((ROOT / 'config/local.json').read_text())
DATABASE_URL = os.environ.get('DATABASE_URL', f"postgresql+psycopg://erp:{quote(LOCAL['db_password'])}@127.0.0.1:{LOCAL['pg_port']}/amazon_erp")
REDIS_URL = os.environ.get('REDIS_URL', f"redis://:{quote(LOCAL['redis_password'])}@127.0.0.1:{LOCAL['redis_port']}/0")
from collections.abc import Mapping,Sequence
class Shops(Mapping):
 def __getitem__(self,key):
  from .store_credentials import sp_config
  return sp_config(key)
 def __iter__(self):
  from .store_credentials import names
  return iter(names())
 def __len__(self):return len(list(iter(self)))
class ShopNames(Sequence):
 def __getitem__(self,i):return list(STORES)[i]
 def __len__(self):return len(STORES)
STORES=Shops()
STORE_NAMES=ShopNames()
MARKETS={'UK':'A1F83G8C2ARO7P','DE':'A1PA6795UKMFR9','FR':'A13V1IB3VIYZZH','IT':'APJ6JRA9NG5V4','ES':'A1RKKUPIHCS9HS','NL':'A1805IZSGTT6HS','BE':'AMEN7PMS3EDWL','SE':'A2NODRKZP88ZB9','PL':'A1C3SOZRARQ6R3','IE':'A28R8C7NBKEWEA'}
ORDER_MARKETS={**MARKETS,'AE':'A2VIGQ35RCS4UG','SA':'A17E79C6D8DWNP'}
