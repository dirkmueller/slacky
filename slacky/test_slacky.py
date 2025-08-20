#!/usr/bin/python3
"""
Copyright (C) 2023 Dirk Müller, SUSE LLC

This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

SPDX-License-Identifier: GPL-2.0-or-later
"""

import collections
import datetime
import json
import re
from unittest.mock import patch

import slacky


def _create_bot() -> slacky.Slacky:
    bot = slacky.Slacky()

    # Patch load_state and save_state to do nothing
    bot.load_state = lambda *args, **kwargs: None
    bot.save_state = lambda *args, **kwargs: None
    bot.repo_publishes = dict()

    bot.container_publish_re = re.compile(r'^SUSE:Containers:SLE-SERVER:')
    bot.container_build_re = re.compile(r'^SUSE:SLE-15-SP.:Update:(BCI|CR)')
    bot.repo_publish_re = re.compile(r'^SUSE:Products:SLE-BCI')
    slacky.CONF = {
        'DEFAULT': {},
        'obs': {'host': 'https://localhost/'},
        'openqa': {'host': 'https://localhost/'},
    }
    return bot


@patch('slacky.post_failure_notification_to_slack', return_value=None)
def test_pending_bs_requests_grouping(mock_post_failure_notification):
    bot = _create_bot()

    bot.bs_requests = {
        1: slacky.bs_Request(
            id=1,
            targetproject='project1',
            targetpackage='package1',
            created_at=datetime.datetime(2023, 1, 2),
        ),
        2: slacky.bs_Request(
            id=2,
            targetproject='project1',
            targetpackage='package2',
            created_at=datetime.datetime(2023, 1, 3),
        ),
    }

    bot.last_interval_check = datetime.datetime(2023, 1, 1)
    bot.check_pending_requests()
    mock_post_failure_notification.assert_called_once_with(
        ':request-changes:',
        '2 hanging requests to project1 / package1, package2 ',
        'https://localhost/project/requests/project1',
    )
    for _, req in bot.bs_requests.items():
        assert req.is_announced


@patch('slacky.post_failure_notification_to_slack', return_value=None)
def test_pending_bs_requests_single(mock_post_failure_notification):
    bot = _create_bot()

    bot.bs_requests = {
        1: slacky.bs_Request(
            id=1,
            targetproject='project1',
            targetpackage='package1',
            created_at=datetime.datetime(2023, 1, 2),
        )
    }
    bot.last_interval_check = datetime.datetime(2023, 1, 1)
    with patch('slacky.datetime') as mock_datetime:
        mock_datetime.now.return_value = datetime.datetime(
            2023, 1, 2
        ) + datetime.timedelta(seconds=90)
        bot.check_pending_requests()

        mock_post_failure_notification.assert_called_once_with(
            ':announcement:',
            'New request to project1 / package1 available for review. ',
            'https://localhost/project/requests/project1',
        )
        mock_post_failure_notification.reset_mock()
        mock_datetime.now.return_value = datetime.datetime(
            2023, 1, 2
        ) + datetime.timedelta(seconds=300)
        bot.check_pending_requests()
        mock_post_failure_notification.assert_not_called()

    bot.last_interval_check = datetime.datetime(2023, 1, 1)
    bot.check_pending_requests()
    mock_post_failure_notification.assert_called_once_with(
        ':request-changes:',
        'Request to project1 / package1 is still open ',
        'https://localhost/project/requests/project1',
    )


@patch('slacky.post_failure_notification_to_slack', return_value=None)
def test_pending_bs_requests_multiple(mock_post_failure_notification):
    bot = _create_bot()

    bot.bs_requests = {
        1: slacky.bs_Request(
            id=1,
            targetproject='project1',
            targetpackage='package1',
            created_at=datetime.datetime(2023, 1, 2),
        ),
        2: slacky.bs_Request(
            id=2,
            targetproject='project1',
            targetpackage='package2',
            created_at=datetime.datetime(2023, 1, 2),
        ),
    }
    bot.last_interval_check = datetime.datetime(2023, 1, 1)
    with patch('slacky.datetime') as mock_datetime:
        mock_datetime.now.return_value = datetime.datetime(
            2023, 1, 2
        ) + datetime.timedelta(seconds=90)
        bot.check_pending_requests()

        mock_post_failure_notification.assert_called_once_with(
            ':announcement:',
            '2 open requests to project1 / package1, package2 for review. ',
            'https://localhost/project/requests/project1',
        )
        mock_post_failure_notification.reset_mock()
        mock_datetime.now.return_value = datetime.datetime(
            2023, 1, 2
        ) + datetime.timedelta(seconds=300)
        bot.check_pending_requests()
        mock_post_failure_notification.assert_not_called()

    bot.last_interval_check = datetime.datetime(2023, 1, 1)
    bot.check_pending_requests()
    mock_post_failure_notification.assert_called_once_with(
        ':request-changes:',
        '2 hanging requests to project1 / package1, package2 ',
        'https://localhost/project/requests/project1',
    )


@patch('slacky.post_failure_notification_to_slack', return_value=None)
def test_declined_bs_requests_single(mock_post_failure_notification):
    bot = _create_bot()

    body = '{"number": 1, "state": "new", "actions": [{"type": "submit", "targetproject": "SUSE:SLE-15-SP6:Update:BCI", "targetpackage": "test"}]}'
    bot.handle_obs_request_event('suse.obs.request.create', json.loads(body))

    mock_post_failure_notification.assert_not_called()

    body = '{"number": 1, "state": "review"}'
    bot.handle_obs_request_event('suse.obs.request.state_change', json.loads(body))
    body = '{"number": 1, "state": "declined"}'
    bot.handle_obs_request_event('suse.obs.request.state_change', json.loads(body))
    mock_post_failure_notification.assert_called_with(
        ':request-changes:',
        'Request to SUSE:SLE-15-SP6:Update:BCI / test got declined.',
        'https://localhost/request/show/1',
    )


@patch('slacky.post_failure_notification_to_slack', return_value=None)
def test_obs_repo_publish(mock_post_failure_notification):
    bot = _create_bot()

    with patch('slacky.datetime') as mock_datetime:
        mock_datetime.now.return_value = datetime.datetime(2023, 1, 2)
        bot.handle_obs_repo_event(
            'suse.obs.repo',
            {
                'state': 'publishing',
                'project': 'SUSE:Containers:SLE-SERVER:15',
                'repo': 'containers',
            },
        )
    assert len(bot.repo_publishes.keys()) == 1
    bot.check_pending_requests()
    mock_post_failure_notification.assert_called_with(
        ':published:',
        'SUSE:Containers:SLE-SERVER:15 / containers is still in publishing state for 3:00:00',
        'https://localhost/project/repository_state/SUSE:Containers:SLE-SERVER:15/containers',
    )


@patch('slacky.post_failure_notification_to_slack', return_value=None)
def test_bci_repo_publish(mock_post_failure_notification):
    bot = _create_bot()
    assert len(bot.repo_publishes.keys()) == 0

    with patch('slacky.datetime') as mock_datetime:
        mock_datetime.now.return_value = datetime.datetime(2023, 1, 2)
        bot.handle_obs_repo_event(
            'suse.obs.repo',
            {
                'state': 'publishing',
                'project': 'SUSE:Products:SLE-BCI:16.0:x86_64',
                'repo': 'images',
            },
        )
    assert len(bot.repo_publishes.keys()) == 1
    with patch('slacky.datetime') as mock_datetime:
        mock_datetime.now.return_value = datetime.datetime(2023, 1, 3)
        bot.handle_obs_repo_event(
            'suse.obs.repo',
            {
                'state': 'published',
                'project': 'SUSE:Products:SLE-BCI:16.0:x86_64',
                'repo': 'images',
            },
        )
    assert len(bot.repo_publishes.keys()) == 1

    bot.check_pending_requests()
    mock_post_failure_notification.assert_called_with(
        ':construction:',
        'SUSE:Products:SLE-BCI:16.0:x86_64 / images has not been published for 5 days, 0:00:00',
        'https://localhost/project/repository_state/SUSE:Products:SLE-BCI:16.0:x86_64/images',
    )


@patch('slacky.post_failure_notification_to_slack', return_value=None)
def test_obs_container_publish(mock_post_failure_notification):
    bot = _create_bot()

    with patch('slacky.datetime') as mock_datetime:
        mock_datetime.now.return_value = datetime.datetime(2023, 1, 2)
        bot.handle_container_event(
            'suse.obs.container.published',
            {
                'project': 'SUSE:Containers:SLE-SERVER:15',
                'repo': 'standard',
                'buildid': '1',
                'container': 'registry.suse.com/suse/sle15:15.5',
            },
        )
        bot.handle_container_event(
            'suse.obs.container.published',
            {
                'project': 'SUSE:Containers:SLE-SERVER:15',
                'repo': 'standard',
                'buildid': '2',
                'container': 'registry.suse.com/suse/nginx:1.21-42.10',
            },
        )

    assert bot.container_publishes == {
        'suse/nginx:1.21': datetime.datetime(2023, 1, 2),
        'suse/sle15:15.5': datetime.datetime(2023, 1, 2),
    }
    with patch('slacky.datetime') as mock_datetime:
        mock_datetime.now.return_value = (
            datetime.datetime(2023, 1, 2)
            + slacky.HANGING_CONTAINER_TAG
            + datetime.timedelta(minutes=5)
        )
        bot.check_pending_requests()
        mock_post_failure_notification.assert_called_with(
            ':question:',
            'These tags were not published after 28 days, 0:00:00: suse/nginx:1.21,suse/sle15:15.5',
            'https://registry.suse.com/',
        )
    mock_post_failure_notification.reset_mock()
    bot.check_pending_requests()
    mock_post_failure_notification.assert_not_called()


@patch('slacky.post_failure_notification_to_slack', return_value=None)
def test_openqa_failure(mock_post_failure_notification):
    bot = _create_bot()

    with patch('slacky.datetime') as mock_datetime:
        mock_datetime.now.return_value = datetime.datetime(2023, 1, 2)
        body = (
            '{"group_id": 444, "BUILD": "repo_23.2", "ARCH": "x86_64", "TEST": "TEST1"}'
        )
        bot.handle_openqa_event('suse.openqa.job.create', json.loads(body))
        body = '{"group_id": 444, "BUILD": "repo_23.2", "ARCH": "aarch64", "TEST": "TEST1"}'
        bot.handle_openqa_event('suse.openqa.job.create', json.loads(body))
        body = '{"group_id": 444, "BUILD": "repo_23.2", "ARCH": "aarch64", "TEST": "TEST1", "result": "failed"}'
        bot.handle_openqa_event('suse.openqa.job.done', json.loads(body))
        body = '{"group_id": 444, "BUILD": "repo_23.2", "ARCH": "x86_64", "TEST": "TEST1", "result": "failed"}'
        bot.handle_openqa_event('suse.openqa.job.done', json.loads(body))
        body = '{"group_id": 444, "BUILD": "repo_23.2", "ARCH": "ppc64le", "TEST": "TEST1"}'
        bot.handle_openqa_event('suse.openqa.job.created', json.loads(body))
        mock_post_failure_notification.assert_not_called()
        mock_datetime.now.return_value = datetime.datetime(
            2023, 1, 2
        ) + datetime.timedelta(minutes=3)
        bot.handle_openqa_event(
            'suse.openqa.job.restart',
            {'group_id': 444, 'BUILD': 'repo_23.2', 'ARCH': 'ppc64le', 'TEST': 'TEST1'},
        )

        mock_datetime.now.return_value = datetime.datetime(
            2023, 1, 2
        ) + datetime.timedelta(minutes=5)
        body = '{"group_id": 444, "BUILD": "repo_23.2", "ARCH": "aarch64", "TEST": "TEST1", "result": "passed"}'
        bot.handle_openqa_event('suse.openqa.job.done', json.loads(body))
        body = '{"group_id": 444, "BUILD": "repo_23.2", "ARCH": "x86_64", "TEST": "TEST1", "result": "failed"}'
        bot.handle_openqa_event('suse.openqa.job.done', json.loads(body))
        body = '{"group_id": 444, "BUILD": "repo_23.2", "ARCH": "ppc64le", "TEST": "TEST1", "result": "passed"}'
        bot.handle_openqa_event('suse.openqa.job.done', json.loads(body))
        bot.check_pending_requests()
        mock_post_failure_notification.assert_not_called()

    assert bot.openqa_jobs == collections.defaultdict(
        list,
        {
            (444, 'repo_23.2'): [
                slacky.openQAJob(
                    test_id='TEST1/x86_64',
                    build='repo_23.2',
                    result='failed',
                    finished_at=datetime.datetime(2023, 1, 2, 0, 5),
                ),
                slacky.openQAJob(
                    test_id='TEST1/aarch64',
                    build='repo_23.2',
                    result='passed',
                    finished_at=datetime.datetime(2023, 1, 2, 0, 5),
                ),
                slacky.openQAJob(
                    test_id='TEST1/ppc64le',
                    build='repo_23.2',
                    result='passed',
                    finished_at=datetime.datetime(2023, 1, 2, 0, 5),
                ),
            ]
        },
    )
    bot.check_pending_requests()
    mock_post_failure_notification.assert_called_with(
        ':openqa:',
        'Build repo_23.2 has 1 failed tests.',
        'https://localhost/tests/overview?build=repo_23.2&groupid=444',
    )
    assert len(bot.openqa_jobs) == 0
