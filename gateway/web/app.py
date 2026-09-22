"""FastAPI 装配。页面路由出 HTML,/api/* 出 JSON。写操作一律 POST + CSRF。"""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse

from .. import config as configmod
from . import control, data, logs
from .auth import COOKIE_NAME, Auth
from .settings import WebSettings, load as load_settings

HERE = Path(__file__).parent
PUBLIC = ('/login', '/static', '/healthz')


def create_app(settings: WebSettings | None = None) -> FastAPI:
    st = settings or load_settings()
    auth = Auth(st)
    app = FastAPI(title='驿站控制台', docs_url=None, redoc_url=None)
    app.state.settings = st
    app.state.auth = auth
    (HERE / 'static').mkdir(exist_ok=True)
    (HERE / 'templates').mkdir(exist_ok=True)
    app.mount('/static', StaticFiles(directory=HERE / 'static'), name='static')
    tpl = Jinja2Templates(directory=str(HERE / 'templates'))

    def logged_in(request: Request) -> bool:
        return auth.verify_cookie(request.cookies.get(COOKIE_NAME))

    def csrf_of(request: Request) -> str:
        return auth.csrf_token(request.cookies.get(COOKIE_NAME))

    @app.middleware('http')
    async def guard(request: Request, call_next):
        path = request.url.path
        if not any(path.startswith(p) for p in PUBLIC):
            if not logged_in(request):
                if path.startswith('/api/'):
                    return JSONResponse({'ok': False, 'message': '没登录'}, status_code=401)
                return RedirectResponse('/login', status_code=302)
            if request.method == 'POST':
                token = request.headers.get('X-CSRF')
                if not token:
                    form = await request.form()
                    token = form.get('csrf')
                    request.scope['_form'] = form        # 已经读过 body,后面复用
                if not auth.verify_csrf(request.cookies.get(COOKIE_NAME), token):
                    return JSONResponse({'ok': False, 'message': 'CSRF 校验不过,刷新页面重试'},
                                        status_code=403)
        return await call_next(request)

    async def form_of(request: Request) -> dict:
        if '_form' in request.scope:
            return dict(request.scope['_form'])
        ctype = request.headers.get('content-type', '')
        if ctype.startswith('application/json'):
            try:
                return await request.json()
            except Exception:
                return {}
        return dict(await request.form())

    def page(name: str, request: Request, **ctx):
        return tpl.TemplateResponse(request, name, {'csrf': csrf_of(request), **ctx})

    # ---- 登录 ----
    @app.get('/login', response_class=HTMLResponse)
    def login_page(request: Request):
        if logged_in(request):
            return RedirectResponse('/', status_code=302)
        return tpl.TemplateResponse(request, 'login.html', {'error': None})

    @app.post('/login')
    async def login(request: Request, password: str = Form('')):
        ip = request.client.host if request.client else '?'
        ok, msg = auth.check_password(ip, password)
        if not ok:
            return tpl.TemplateResponse(request, 'login.html', {'error': msg}, status_code=401)
        resp = RedirectResponse('/', status_code=302)
        resp.set_cookie(COOKIE_NAME, auth.issue_cookie(), httponly=True, samesite='lax',
                        max_age=st.session_hours * 3600)
        return resp

    @app.post('/logout')
    def logout():
        resp = RedirectResponse('/login', status_code=302)
        resp.delete_cookie(COOKIE_NAME)
        return resp

    @app.get('/healthz')
    def healthz():
        return {'ok': True}

    # ---- 页面 ----
    @app.get('/', response_class=HTMLResponse)
    def index(request: Request):
        return page('index.html', request, instances=data.instances())

    @app.get('/i/{inst}', response_class=HTMLResponse)
    def instance_page(request: Request, inst: str, tab: str = 'overview'):
        if inst not in data.list_instances():
            raise HTTPException(404, f'没有实例 {inst}')
        return page('instance.html', request, inst=inst, tab=tab,
                    summary=data.instance_summary(inst), fields=control.field_meta(),
                    streams=logs.STREAMS)

    @app.get('/new', response_class=HTMLResponse)
    def new_page(request: Request):
        return page('new.html', request, fields=control.field_meta())

    # ---- 只读接口 ----
    @app.get('/api/instances')
    def api_instances():
        return data.instances()

    def _guard_inst(inst: str):
        if inst not in data.list_instances():
            raise HTTPException(404, f'没有实例 {inst}')

    @app.get('/api/i/{inst}/overview')
    def api_overview(inst: str):
        _guard_inst(inst)
        try:
            return data.overview(inst)
        except data.DbBroken as e:
            raise HTTPException(409, str(e))

    @app.get('/api/i/{inst}/cards')
    def api_cards(inst: str, state: str | None = None, page_no: int = Query(1, alias='page'),
                  size: int = 20):
        _guard_inst(inst)
        try:
            return data.cards(inst, state, page_no, min(size, 100))
        except data.DbBroken as e:
            raise HTTPException(409, str(e))

    @app.get('/api/i/{inst}/cards/{card_id}')
    def api_card(inst: str, card_id: int):
        _guard_inst(inst)
        try:
            return data.card(inst, card_id)
        except KeyError as e:
            raise HTTPException(404, str(e))
        except data.DbBroken as e:
            raise HTTPException(409, str(e))

    @app.get('/api/i/{inst}/session')
    def api_session(inst: str):
        _guard_inst(inst)
        try:
            return data.session(inst)
        except data.DbBroken as e:
            raise HTTPException(409, str(e))

    @app.get('/api/i/{inst}/config')
    def api_config_read(inst: str):
        _guard_inst(inst)
        return {'values': control.config_read(inst), 'fields': control.field_meta()}

    @app.get('/api/i/{inst}/attach/{key}')
    def api_attach(inst: str, key: str):
        _guard_inst(inst)
        from fastapi.responses import FileResponse
        try:
            p = data.attachment_path(inst, key)
        except ValueError as e:
            raise HTTPException(400, str(e))
        if not p.exists():
            raise HTTPException(404, '附件不在本地')
        return FileResponse(p)

    # ---- 日志 SSE ----
    @app.get('/api/i/{inst}/logs/{stream}')
    async def api_logs(request: Request, inst: str, stream: str, since: int = 0, q: str | None = None):
        _guard_inst(inst)
        if stream not in logs.STREAMS:
            raise HTTPException(400, f'不认识的日志流: {stream}')

        async def gen():
            # next() 在线程池里跑,断线时这边可能正被取消,而线程还堵在里面。
            # 这种时候 it.close() 会撞上「generator already executing」(2026-09-21 实测
            # 500),所以只置 stop,让生成器自己从循环里退出来收尾。
            stop = threading.Event()
            it = logs.stream_events(inst, stream, since, q, stop=stop)
            loop = asyncio.get_running_loop()
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    ev = await loop.run_in_executor(None, lambda: next(it, None))
                    if ev is None:
                        break
                    yield {'event': 'log', 'data': json.dumps(ev, ensure_ascii=False)}
            finally:
                stop.set()

        return EventSourceResponse(gen())

    # ---- 写操作 ----
    @app.post('/api/i/{inst}/action/{name}')
    async def api_action(request: Request, inst: str, name: str):
        _guard_inst(inst)
        form = await form_of(request)
        r = control.action(inst, name, **{k: v for k, v in form.items() if k != 'csrf'})
        return JSONResponse(r, status_code=200 if r['ok'] else 400)

    @app.post('/api/i/{inst}/config')
    async def api_config_write(request: Request, inst: str):
        _guard_inst(inst)
        form = await form_of(request)
        form.pop('csrf', None)
        r = control.config_write(inst, form)
        return JSONResponse(r, status_code=200 if r['ok'] else 400)

    @app.post('/api/new/init')
    async def api_new_init(request: Request):
        f = await form_of(request)
        r = control.wizard_init(str(f.get('inst', '')).strip(), str(f.get('project_dir', '')).strip(),
                                (f.get('skill') or None))
        return JSONResponse(r, status_code=200 if r['ok'] else 400)

    @app.post('/api/new/verify')
    async def api_new_verify(request: Request):
        f = await form_of(request)
        inst = str(f.pop('inst', '')).strip()
        f.pop('csrf', None)
        _guard_inst(inst)
        r = control.wizard_verify(inst, f)
        return JSONResponse(r, status_code=200 if r.get('ok') else 400)

    @app.post('/api/new/enable')
    async def api_new_enable(request: Request):
        f = await form_of(request)
        inst = str(f.get('inst', '')).strip()
        _guard_inst(inst)
        r = control.wizard_enable(inst)
        return JSONResponse(r, status_code=200 if r['ok'] else 400)

    return app
