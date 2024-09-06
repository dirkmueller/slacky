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

import argparse
import collections
import configparser
import json
import logging as LOG
import os
import pickle
import random
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pika
import requests
from pika.adapters.blocking_connection import BlockingChannel

CONF = configparser.ConfigParser(strict=False)
OPENQA_GROUPS_FILTER: tuple[int] = (538, 475, 443, 442, 428)


def post_failure_notification_to_slack(status, body, link_to_failure) -> None:
    """Post a message to slack with the given parameters by using a webhook."""
    LOG.debug(
        f'post_failure_notification_to_slack({status}, {body}, {link_to_failure})'
    )

    resp = requests.post(
        url=CONF['DEFAULT']['slack_trigger_url'],
        headers={'Content-Type': 'application/json'},
        json={'status': status, 'body': body, 'link_to_failure': link_to_failure},
    )
    try:
        resp.raise_for_status()
    except requests.HTTPError as err:
        LOG.error(f'Failed to post failure notification to slack: {err}')


@dataclass
class openQAJob:
    """Track the state of a openQA job identified by id"""

    id: int
    build: str
    result: str


@dataclass
class bs_Request:
    """Track build service requests identified by id"""

    id: int
    targetproject: str
    targetpackage: str
    created_at: datetime
    is_announced: bool = False


@dataclass
class repo_publish:
    """Track repository publishing"""

    project: str
    repository: str
    state: str
    state_changed: datetime
    is_announced: bool = False


class Slacky:
    # when adding more state, please update load_state()
    openqa_jobs = collections.defaultdict(list)
    bs_requests = collections.defaultdict(None)
    repo_publishes = collections.defaultdict(repo_publish)
    last_interval_check = datetime.now()

    def handle_openqa_event(self, method, body):
        """Find failed jobs without pending jobs and then post a message to slack."""
        msg = json.loads(body)
        if msg.get('group_id') not in OPENQA_GROUPS_FILTER:
            return

        build_id: str = msg.get('BUILD')

        LOG.info(f' [x] {method.routing_key!r}:{msg!r}')
        if 'suse.openqa.job.create' in method.routing_key and msg.get('id'):
            self.openqa_jobs[build_id].append(
                openQAJob(id=msg['id'], build=build_id, result='pending')
            )
            LOG.info(f"Job {build_id}/{msg['id']} created (pending)")
        elif (
            'suse.openqa.job.done' in method.routing_key
            and build_id in self.openqa_jobs
        ):
            for job in self.openqa_jobs[build_id]:
                if job.id == msg['id']:
                    job.result = msg['result']

            # Find for any failures
            results = collections.Counter(j.result for j in self.openqa_jobs[build_id])
            LOG.info(f'Job ended - results: {results}')
            if not results.get('pending') and results.get('failed'):
                body: str = f"Build {build_id} has {results['failed']} failed tests."
                post_failure_notification_to_slack(
                    ':openqa:',
                    body,
                    f"{CONF['openqa']['host']}tests/overview?build={build_id}&groupid={msg['group_id']}",
                )

            if not results.get('pending'):
                # Clear the build from pending jobs
                del self.openqa_jobs[build_id]

    def handle_obs_package_event(self, method, body):
        """Post any build failures for the configured projects to slack."""
        msg = json.loads(body)

        if (
            not self.project_re.match(msg.get('project', ''))
            or msg.get('previouslyfailed') == '1'
        ):
            return

        if 'suse.obs.package.build_fail' in method.routing_key:
            LOG.info(
                f"obs build fail {msg['project']}/{msg['package']}/{msg['repository']}/{msg['arch']}"
            )
            post_failure_notification_to_slack(
                ':obs:',
                f"{msg['project']}/{msg['package']}/{msg['repository']}/{msg['arch']} failed to build.",
                f"{CONF['obs']['host']}{msg['project']}/{msg['package']}/{msg['repository']}/{msg['arch']}",
            )

    def handle_obs_repo_event(self, method, body):
        """Post any build failures for the configured projects to slack."""
        msg = json.loads(body)

        if not self.repo_re.match(msg.get('project')):
            return

        prjrepo = f"{msg['project']}/{msg['repo']}"
        LOG.info(f'repo event for {prjrepo}')
        if msg['state'] == 'published':
            del self.repo_publishes[prjrepo]
            return

        with self.repo_publishes[prjrepo] as repo:
            repo.project = msg['project']
            repo.repository = msg['repo']
            repo.state = msg['state']
            repo.state_changed = datetime.now()

    def handle_obs_request_event(self, method, body):
        """Warn when requests get declined, track them for hang detection."""
        msg = json.loads(body)

        if 'suse.obs.request.create' in method.routing_key:
            for action in msg['actions']:
                if action['type'] == 'submit' and 'BCI' in action['targetproject']:
                    LOG.info(
                        f"found new submitrequest against {action['targetproject']}: id {msg['number']}"
                    )
                    bs_request = bs_Request(
                        id=msg['number'],
                        targetproject=action['targetproject'],
                        targetpackage=action['targetpackage'],
                        created_at=datetime.now(),
                    )
                    self.bs_requests[msg['number']] = bs_request
                    post_failure_notification_to_slack(
                        ':announcement:',
                        f'{bs_request.targetproject} / {bs_request.targetpackage}: New request to review!',
                        f"{CONF['obs']['host']}/request/show/{bs_request.id}",
                    )

        if 'suse.obs.request.state_change' in method.routing_key:
            bs_request = self.bs_requests.get(msg['number'])
            if bs_request:
                if msg['state'] in ('declined',):
                    post_failure_notification_to_slack(
                        ':request-changes:',
                        f'Request to {bs_request.targetproject} / {bs_request.targetpackage} got declined.',
                        f"{CONF['obs']['host']}/request/show/{bs_request.id}",
                    )
                    bs_request.is_announced = True
                if msg['state'] in ('accepted', 'revoked', 'superseded'):
                    LOG.info(f"request {msg['number']} entered final state.")
                    del self.bs_requests[msg['number']]

    def check_pending_requests(self):
        """Announce for things that are hanging around"""
        for reqid, req in self.bs_requests.items():
            if (
                not req.is_announced
                and (datetime.now() - req.created_at).total_seconds() > 4 * 60 * 60
            ):
                post_failure_notification_to_slack(
                    ':announcement',
                    f'{req.targetproject} / {req.targetpackage}: is waiting for review!',
                    f"{CONF['obs']['host']}/request/show/{req.id}",
                )
                req.is_announced = True

        for prjrepo, repo in self.repo_publishes.items():
            if (
                not repo.is_announced
                and (datetime.now() - repo.state_changed).total_seconds() > 15 * 60
            ):
                post_failure_notification_to_slack(
                    ':published:',
                    f'{repo.project} / {repo.repository}: is not published after a while!',
                    f"{CONF['obs']['host']}/repositories/{repo.project}/{repo.repository}",
                )
                repo.is_announced = True

    def load_state(self) -> None:
        """Restore persisted from a previously launched slacky"""
        state_file = Path(__file__).resolve().parent / 'state.pickle'
        if state_file.is_file():
            with open(Path(__file__).resolve().parent / 'state.pickle', 'rb') as f:
                data = pickle.load(f)
                # copy over the state from a previous launched slacky
                self.openqa_jobs = data.openqa_jobs
                LOG.info(f'Loaded state(openqa_jobs = {self.openqa_jobs})')
                if data.bs_requests:
                    self.bs_requests = data.bs_requests
                    LOG.info(f'Loaded state(bs_requests = {self.bs_requests})')
                self.bs_requests = data.bs_requests
                if data.repo_publishes:
                    self.repo_publishes = data.repo_publishes
                    LOG.info(f'Loaded state(repo_publish = {self.repo_publishes})')

    def save_state(self) -> None:
        """pickle the slacky state for future instance preservation"""
        with open(Path(__file__).resolve().parent / 'state.pickle', 'wb') as f:
            pickle.dump(self, f)
            LOG.info('Saved state to state.pickle')

    def run(self):
        """pubsub subscribe to events posted on the AMPQ channel."""
        channel: BlockingChannel = pika.BlockingConnection(
            pika.URLParameters(CONF['DEFAULT']['listen_url'])
        ).channel()
        channel.exchange_declare(
            exchange='pubsub', exchange_type='topic', passive=True, durable=False
        )
        queue_name = channel.queue_declare('', exclusive=True).method.queue
        channel.queue_bind(exchange='pubsub', queue=queue_name, routing_key='#')

        self.load_state()
        self.project_re = re.compile(CONF['obs']['project_re'])
        self.repo_re = re.compile(CONF['obs']['repo_re'])

        def callback(_, method, _unused, body) -> None:
            """Generic dispatcher for events posted on the AMPQ channel."""

            if (datetime.now() - self.last_interval_check).total_seconds() > 0:
                self.check_pending_requests()
                self.last_interval_check = datetime.now()

            if method.routing_key.startswith('suse.openqa.job'):
                self.handle_openqa_event(method, body)
            elif method.routing_key.startswith('suse.obs.package'):
                self.handle_obs_package_event(method, body)
            elif method.routing_key.startswith('suse.obs.request'):
                self.handle_obs_request_event(method, body)
            elif method.routing_key.startswith('suse.obs.repo'):
                self.handle_obs_repo_event(method, body)
            elif not method.routing_key.startswith(
                'suse.obs.metrics'
            ) and 'Containers' in str(body):
                LOG.info(f' [x] {method.routing_key!r}:{body!r}')

        channel.basic_consume(queue_name, callback, auto_ack=True)
        try:
            print(' [*] Waiting for events. To exit press CTRL+C')
            channel.start_consuming()
        except KeyboardInterrupt:
            channel.stop_consuming()
            self.save_state()
            LOG.info('State saved!')
            sys.exit(0)


def main():
    parse = argparse.ArgumentParser(
        description='Bot to forward BCI pipeline failures to Slack'
    )
    parse.add_argument('-d', '--debug', action='store_true')

    args = parse.parse_args()
    LOG.basicConfig(level=LOG.DEBUG if args.debug else LOG.INFO)

    with open(os.path.expanduser('~/.config/slacky'), encoding='utf8') as f:
        CONF.read_file(f)

    while True:
        slacky = Slacky()
        try:
            slacky.run()
        except (pika.exceptions.ConnectionClosed, pika.exceptions.AMQPHeartbeatTimeout):
            time.sleep(random.randint(10, 100))


if __name__ == '__main__':
    main()
