"""Bounded local tasks and actual progress events; no invented percentages."""
import copy
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix='research')
_LOCK = threading.RLock()
_JOBS = {}


def submit(kind, function):
    with _LOCK:
        if sum(j['status'] in {'queued', 'running'} for j in _JOBS.values()) >= 2:
            raise ValueError('已有两个任务执行中，请等待完成后再试')
        for key in list(_JOBS):
            if _JOBS[key]['status'] not in {'queued', 'running'} and (time.time() - _JOBS[key]['started_at'] > 1800 or len(_JOBS) >= 20):
                del _JOBS[key]
        identity = uuid.uuid4().hex
        job = {'job_id': identity, 'kind': kind, 'status': 'queued', 'started_at': time.time(), 'events': []}
        _JOBS[identity] = job

    def progress(event):
        with _LOCK:
            job['events'].append(dict(event, at=round(time.time() - job['started_at'], 2)))
            job['events'] = job['events'][-40:]

    def execute():
        with _LOCK:
            job['status'] = 'running'
        try:
            result = function(progress)
            with _LOCK:
                job.update(status='succeeded' if result.get('ok') else 'failed', result=result)
        except Exception as exc:
            with _LOCK:
                job.update(status='failed', result={'ok': False, 'error': '任务执行失败：' + type(exc).__name__})
        finally:
            with _LOCK:
                job['finished_at'] = time.time()
    _POOL.submit(execute)
    return {'ok': True, 'job_id': identity}


def get(identity):
    with _LOCK:
        if identity not in _JOBS:
            raise ValueError('任务不存在或已过期，请从历史记录恢复完成的研究')
        job = copy.deepcopy(_JOBS[identity])
    job['elapsed_seconds'] = round(job.get('finished_at', time.time()) - job['started_at'], 1)
    return {'ok': True, **job}
