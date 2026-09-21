import pytest

from gateway.web import settings as st


def test_write_initial_and_load(inst_env, tmp_path):
    p = tmp_path / 'config' / 'web.toml'
    path, pw = st.write_initial(p, port=9999)
    assert oct(path.stat().st_mode)[-3:] == '600' and len(pw) >= 12
    s = st.load(p)
    assert s.port == 9999 and s.password == pw and s.host == '0.0.0.0' and s.session_hours == 72


def test_secret_key_generated_once_and_persisted(tmp_path):
    p = tmp_path / 'web.toml'
    st.write_initial(p)
    s1 = st.load(p)
    assert len(s1.secret_key) == 64            # 32 字节 hex
    s2 = st.load(p)
    assert s2.secret_key == s1.secret_key      # 重启不掉线:密钥写回了文件


def test_missing_password_refuses(tmp_path):
    p = tmp_path / 'web.toml'
    p.write_text('port = 8760\n')
    with pytest.raises(st.WebConfigError) as e:
        st.load(p)
    assert 'password' in str(e.value)


def test_missing_file(tmp_path):
    with pytest.raises(st.WebConfigError):
        st.load(tmp_path / 'nope.toml')


def test_default_path_follows_config_dir(inst_env):
    assert st.default_path().name == 'web.toml'
    assert str(st.default_path().parent).endswith('config')
