"""py.test fixtures"""

# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.

import logging
from getpass import getuser
from subprocess import TimeoutExpired
import time
from unittest import mock
from pytest import fixture, raises
from tornado import ioloop, gen

from .. import orm
from ..utils import random_port

from .mocking import MockHub
from .test_services import mockservice_cmd

import jupyterhub.services.service

# global db session object
_db = None

@fixture
def db():
    """Get a db session"""
    global _db
    if _db is None:
        _db = orm.new_session_factory('sqlite:///:memory:')()
        user = orm.User(
            name=getuser(),
        )
        user.servers.append(orm.Server())
        _db.add(user)
        _db.commit()
    return _db


@fixture
def io_loop():
    """Get the current IOLoop"""
    loop = ioloop.IOLoop()
    loop.make_current()
    return loop


@fixture(scope='module')
def app(request):
    """Mock a jupyterhub app for testing"""
    mocked_app = MockHub.instance(log_level=logging.DEBUG)
    mocked_app.start([])


    def fin():
        # disconnect logging during cleanup because pytest closes captured FDs prematurely
        mocked_app.log.handlers = []

        MockHub.clear_instance()
        mocked_app.stop()
    request.addfinalizer(fin)
    return mocked_app


# mock services for testing.
# Shorter intervals, etc.
class MockServiceSpawner(jupyterhub.services.service._ServiceSpawner):
    poll_interval = 1

_mock_service_counter = 0

def _mockservice(request, app, url=False):
    global _mock_service_counter
    _mock_service_counter += 1
    name = 'mock-service-%i' % _mock_service_counter
    spec = {
        'name': name,
        'command': mockservice_cmd,
        'admin': True,
    }
    if url:
        spec['url'] = 'http://127.0.0.1:%i' % random_port()

    with mock.patch.object(jupyterhub.services.service, '_ServiceSpawner', MockServiceSpawner):
        app.services = [spec]
        app.init_services()
        assert name in app._service_map
        service = app._service_map[name]
        @gen.coroutine
        def start():
            # wait for proxy to be updated before starting the service
            yield app.proxy.add_all_services(app._service_map)
            service.start()
        app.io_loop.add_callback(start)
        def cleanup():
            service.stop()
            app.services[:] = []
            app._service_map.clear()
        request.addfinalizer(cleanup)
        for i in range(20):
            if not getattr(service, 'proc', False):
                time.sleep(0.2)
        # ensure process finishes starting
        with raises(TimeoutExpired):
            service.proc.wait(1)
        if url:
            ioloop.IOLoop().run_sync(service.server.wait_up)
    return service


@fixture
def mockservice(request, app):
    """Mock a service with no external service url"""
    yield _mockservice(request, app, url=False)


@fixture
def mockservice_url(request, app):
    """Mock a service with its own url to test external services"""
    yield _mockservice(request, app, url=True)


@fixture
def no_patience(app):
    """Set slow-spawning timeouts to zero"""
    with mock.patch.dict(app.tornado_application.settings,
                         {'slow_spawn_timeout': 0,
                          'slow_stop_timeout': 0}):
        yield
