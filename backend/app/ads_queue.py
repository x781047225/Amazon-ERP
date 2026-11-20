"""Administrator-owned SQS settings. Never return or log AWS credentials."""
import re
from uuid import uuid4
from urllib.parse import urlsplit
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from .auth import principal
from .db import Session
from .store_credentials import encode, decode
from .ads_queue_models import AdsMessageQueue, utcnow

SECRET_KEYS = ('access_key_id', 'secret_access_key', 'session_token')

def admin():
    p = principal.get() or {}
    if not p.get('is_admin') or p.get('token_id'):
        raise HTTPException(403, '广告消息队列包含全店铺授权，请使用管理员网页登录设置')

router = APIRouter(prefix='/api/settings/ads-queue', dependencies=[Depends(admin)])

def checked_row(db, queue_id, revision=None):
    row = db.scalar(select(AdsMessageQueue).where(AdsMessageQueue.id == queue_id).with_for_update())
    if not row:
        raise HTTPException(404, '队列配置不存在')
    if revision is not None and revision != row.revision:
        raise HTTPException(409, '配置已被修改，请刷新后重试')
    return row

def config_input(body, previous=None):
    previous = previous or {}
    limits = {'name':80, 'region':40, 'queue_url':400, 'auth_mode':20, 'profile_name':100,
              'access_key_id':128, 'secret_access_key':256, 'session_token':8192}
    for k, limit in limits.items():
        if k in body and (not isinstance(body[k], str) or len(body[k]) > limit):
            raise HTTPException(422, '配置字段格式或长度不正确，请检查后重试')
    result = {k:str(body.get(k, '')).strip() for k in limits}
    if not result['name']:
        raise HTTPException(422, '请填写队列名称')
    region = result['region']
    if not re.fullmatch(r'[a-z]{2}(?:-[a-z]+)+-\d', region):
        raise HTTPException(422, '请填写正确的 AWS Region，例如 eu-west-1')
    try:
        url = urlsplit(result['queue_url'])
    except ValueError:
        raise HTTPException(422, 'Queue URL 格式不正确，请复制完整的 SQS 队列地址') from None
    suffix = 'amazonaws.com.cn' if region.startswith('cn-') else 'amazonaws.com'
    if (url.scheme != 'https' or url.netloc != f'sqs.{region}.{suffix}' or url.query or url.fragment
            or not re.fullmatch(r'/\d{12}/[A-Za-z0-9_-]{1,80}', url.path)):
        raise HTTPException(422, '请填写与 Region 一致的标准 SQS Queue URL；不支持 FIFO 或自定义地址')
    if result['auth_mode'] not in ('keys', 'profile', 'role'):
        raise HTTPException(422, '请选择 AWS 授权方式')
    if result['auth_mode'] == 'keys':
        clear = body.get('clear_session_token', False)
        for k in SECRET_KEYS:
            result[k] = result[k] or (previous.get(k, '') if previous.get('auth_mode') == 'keys' else '')
        if clear is True:
            result['session_token'] = ''
        if not result['access_key_id'] or not result['secret_access_key']:
            raise HTTPException(422, '请填写 Access Key ID 和 Secret Access Key')
        if bool(body.get('access_key_id')) != bool(body.get('secret_access_key')):
            raise HTTPException(422, '更换授权时请同时填写 Access Key ID 和 Secret Access Key')
    else:
        for k in SECRET_KEYS:
            result[k] = ''
    if result['auth_mode'] == 'profile' and not re.fullmatch(r'[\w .@-]{1,100}', result['profile_name']):
        raise HTTPException(422, '请填写本机 AWS Profile 名称')
    return result

def public(row):
    config = decode(row)
    fresh = bool(row.heartbeat_at and (utcnow() - row.heartbeat_at).total_seconds() < 90)
    return dict(id=row.id, name=row.name, region=row.region, queue_url=row.queue_url,
                auth_mode=config['auth_mode'], profile_name=config.get('profile_name', ''),
                configured={k:bool(config.get(k)) for k in SECRET_KEYS}, revision=row.revision,
                enabled=row.enabled, checked_at=row.checked_at, check_error=row.check_error,
                connection_ok=row.checked_revision == row.revision,
                attributes=row.attributes, heartbeat_at=row.heartbeat_at, received_at=row.received_at,
                received_count=row.received_count, duplicate_count=row.duplicate_count,
                quarantined_count=row.quarantined_count, datasets=row.datasets,
                consumer_error=row.consumer_error,
                consumer_state='paused' if not row.enabled else 'error' if row.consumer_error else 'running' if fresh else 'waiting')

@router.get('')
def list_queues():
    with Session() as db:
        return {'rows':[public(r) for r in db.scalars(select(AdsMessageQueue).order_by(AdsMessageQueue.name))]}

@router.post('')
def save_queue(body:dict):
    try:
        with Session.begin() as db:
            if body.get('id'):
                if not isinstance(body.get('revision'), int):
                    raise HTTPException(422, '请刷新配置后重试')
                row = checked_row(db, str(body['id']), body['revision'])
                if row.enabled:
                    raise HTTPException(409, '请先暂停消费，再修改队列配置')
                config = config_input(body, decode(row))
                if row.received_count and config['queue_url'] != row.queue_url:
                    raise HTTPException(409, '已有接收记录，请新增队列以保留原队列的记录')
                row.revision += 1
            else:
                config = config_input(body)
                row = AdsMessageQueue(id=str(uuid4()), revision=1)
                db.add(row)
            row.name, row.region, row.queue_url = config['name'], config['region'], config['queue_url']
            row.encrypted = encode(config)
            row.enabled = False
            row.checked_revision = None
            row.checked_at = None
            row.check_error = None
            row.attributes = {}
            row.consumer_error = None
            row.updated_at = utcnow()
            db.flush()
            return public(row)
    except IntegrityError:
        raise HTTPException(409, '这个队列已配置，请编辑已有记录') from None

def make_client(config):
    import boto3
    from botocore.config import Config
    mode = config['auth_mode']
    kwargs = {}
    if mode == 'keys':
        kwargs = dict(aws_access_key_id=config['access_key_id'], aws_secret_access_key=config['secret_access_key'],
                      aws_session_token=config.get('session_token') or None)
    session = boto3.Session(profile_name=config.get('profile_name') if mode == 'profile' else None,
                            region_name=config['region'], **kwargs)
    # Explicit endpoint prevents custom endpoint environment overrides and credential leakage.
    host = urlsplit(config['queue_url']).netloc
    return session.client('sqs', endpoint_url='https://' + host,
                          config=Config(connect_timeout=5, read_timeout=30, retries={'max_attempts':1, 'mode':'standard'}))

def friendly_error(exc):
    code = getattr(exc, 'response', {}).get('Error', {}).get('Code', '')
    if code in ('AccessDenied', 'AccessDeniedException', 'KMS.AccessDeniedException', 'KmsAccessDenied'):
        return 'AWS 拒绝访问，请检查此队列的 IAM 权限和 KMS 解密权限'
    if code in ('InvalidClientTokenId', 'SignatureDoesNotMatch', 'UnrecognizedClientException'):
        return 'AWS 授权无效，请检查 Access Key 和 Secret Key'
    if code in ('ExpiredToken', 'ExpiredTokenException'):
        return 'AWS 临时授权已过期，请更新 Session Token'
    if code in ('AWS.SimpleQueueService.NonExistentQueue', 'QueueDoesNotExist'):
        return '队列不存在或无权访问，请检查 Region、Queue URL 和授权账号'
    if type(exc).__name__ in ('NoCredentialsError', 'PartialCredentialsError', 'ProfileNotFound', 'TokenRetrievalError'):
        return '未找到可用的 AWS 授权，请检查本机 Profile 或填写密钥'
    return '暂时无法连接 AWS 消息队列，请检查网络、Region 和授权后重试'

def read_attributes(client, url):
    attrs = client.get_queue_attributes(QueueUrl=url, AttributeNames=[
        'QueueArn', 'ApproximateNumberOfMessages', 'ApproximateNumberOfMessagesNotVisible',
        'MessageRetentionPeriod', 'RedrivePolicy'])['Attributes']
    return {'arn':attrs.get('QueueArn', ''), 'waiting':int(attrs.get('ApproximateNumberOfMessages', 0)),
            'inflight':int(attrs.get('ApproximateNumberOfMessagesNotVisible', 0)),
            'retention_seconds':int(attrs.get('MessageRetentionPeriod', 0)),
            'has_dead_letter_queue':bool(attrs.get('RedrivePolicy')), 'updated_at':utcnow().isoformat()}

@router.post('/{queue_id}/test')
def test_connection(queue_id:str, body:dict):
    with Session() as db:
        row = checked_row(db, queue_id, body.get('revision'))
        revision, config = row.revision, decode(row)
    client = None
    error = None
    attrs = None
    try:
        client = make_client(config)
        attrs = read_attributes(client, config['queue_url'])
    except Exception as exc:
        error = friendly_error(exc)
    finally:
        if client:
            client.close()
    with Session.begin() as db:
        row = checked_row(db, queue_id, revision)
        row.checked_at, row.check_error = utcnow(), error
        row.checked_revision = None if error else revision
        if attrs is not None:
            row.attributes = attrs
        return {'ok':not error, 'message':error or '连接正常；尚未接收或删除队列消息', 'row':public(row)}

@router.post('/{queue_id}/consumption')
def consumption(queue_id:str, body:dict):
    if not isinstance(body.get('enabled'), bool) or not isinstance(body.get('revision'), int):
        raise HTTPException(422, '请刷新后设置消费开关')
    with Session.begin() as db:
        row = checked_row(db, queue_id, body['revision'])
        if body['enabled']:
            if row.checked_revision != row.revision or not row.checked_at or (utcnow()-row.checked_at).total_seconds() > 900:
                raise HTTPException(409, '请先检查连接，再开启消费')
            if body.get('exclusive_consumer') is not True:
                raise HTTPException(422, '请确认此队列没有其他程序同时消费，避免消息被分流')
        row.enabled = body['enabled']
        row.revision += 1
        row.checked_revision = row.revision if row.checked_revision is not None else None
        row.consumer_error = None
        row.heartbeat_at = None
        row.updated_at = utcnow()
        return public(row)
