"""python -m gateway.web —— systemd 单元的入口。"""
import logging

import uvicorn

from .app import create_app
from .settings import load


def main() -> int:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    st = load()
    uvicorn.run(create_app(st), host=st.host, port=st.port, log_level='info')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
