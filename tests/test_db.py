from gateway import db as dbm
from gateway.events import EventRow


def ev(mid, t=1000, **kw):
    d = dict(message_id=mid, chat_id='oc_group1', chat_type='group', create_time=t, sender_id='ou_a',
             sender_type='user', text='hi', mentions=[{'key': '@_user_1', 'id': 'ou_bot', 'name': 'bot'}])
    d.update(kw)
    return EventRow(**d)


def test_insert_idempotent(db):
    assert dbm.insert_event(db, ev('om_1')) is True
    assert dbm.insert_event(db, ev('om_1')) is False
    assert len(dbm.new_events(db)) == 1
    assert dbm.members(db) == {'bot': 'ou_bot'}


def test_issue_flow(db):
    dbm.insert_event(db, ev('om_1'))
    iid = dbm.create_issue(db, 'oc_group1', None, 'ou_a', 'k', 1000)
    dbm.add_issue_message(db, iid, 'om_1', 1000)
    assert dbm.get_event(db, 'om_1')['card_state'] == 'carded'
    assert dbm.issue_for_message(db, 'om_1')['issue_id'] == iid
    dbm.update_issue(db, iid, state='answering')
    assert dbm.get_issue(db, iid)['state'] == 'answering'
    assert [r['message_id'] for r in dbm.issue_messages(db, iid)] == ['om_1']


def test_session_rotate(db):
    assert dbm.session_get(db) is None
    a = dbm.session_rotate(db, 'init')
    dbm.session_mark_used(db, {'x': 1})
    b = dbm.session_rotate(db, 'resume_failed')
    s = dbm.session_get(db)
    assert s['session_id'] == b and s['prev_id'] == a and s['fresh'] == 1 and s['usage'] is None


def test_cursor_monotonic(db):
    dbm.set_cursor(db, 'oc_1', 500); dbm.set_cursor(db, 'oc_1', 300)
    assert dbm.get_cursor(db, 'oc_1') == 500
