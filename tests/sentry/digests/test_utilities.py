from __future__ import absolute_import

from datetime import timedelta
from django.utils import timezone

from sentry.api.fields.actor import Actor
from sentry.digests.notifications import build_digest, event_to_record
from sentry.digests.utilities import (
    build_events_by_actor,
    convert_actors_to_user_set,
    get_events_from_digest,
    get_personalized_digests,
    sort_records,
    team_actors_to_user_ids,
)
from sentry.models import OrganizationMemberTeam, ProjectOwnership, Team, User
from sentry.ownership.grammar import Rule, Owner, Matcher, dump_schema
from sentry.testutils import TestCase


class UtilitiesHelpersTestCase(TestCase):
    def test_get_events_from_digest(self):
        project = self.create_project()
        rule = project.rule_set.all()[0]
        events = [
            self.create_event(group=self.create_group(project=project)),
            self.create_event(group=self.create_group(project=project)),
            self.create_event(group=self.create_group(project=project)),
            self.create_event(group=self.create_group(project=project)),
            self.create_event(group=self.create_group(project=project)),
        ]
        digest = build_digest(
            project,
            (
                event_to_record(events[4], (rule, )),
                event_to_record(events[3], (rule, )),
                event_to_record(events[2], (rule, )),
                event_to_record(events[1], (rule, )),
                event_to_record(events[0], (rule, )),
            ),
        )

        assert get_events_from_digest(digest) == set(events)

    def test_team_actors_to_user_ids(self):
        team1 = self.create_team()
        team2 = self.create_team()
        team3 = self.create_team()  # team with no active members
        users = [self.create_user() for i in range(0, 8)]

        self.create_member(user=users[0], organization=self.organization, teams=[team1])
        self.create_member(user=users[1], organization=self.organization, teams=[team1])
        self.create_member(user=users[2], organization=self.organization, teams=[team1])
        self.create_member(user=users[3], organization=self.organization, teams=[team1, team2])
        self.create_member(user=users[4], organization=self.organization, teams=[team2, self.team])
        self.create_member(user=users[5], organization=self.organization, teams=[team2])

        # Inactive member
        member6 = self.create_member(
            user=users[6],
            organization=self.organization,
            teams=[
                team2,
                team3])
        team_member6 = OrganizationMemberTeam.objects.filter(organizationmember_id=member6.id)
        for team_member in team_member6:
            team_member.update(is_active=False)
        # Member without teams
        self.create_member(user=users[7], organization=self.organization, teams=[])

        team_actors = [Actor(team1.id, Team), Actor(team2.id, Team), Actor(team3.id, Team)]
        user_ids = [user.id for user in users]

        assert team_actors_to_user_ids(team_actors, user_ids) == {
            team1.id: set([users[0].id, users[1].id, users[2].id, users[3].id]),
            team2.id: set([users[3].id, users[4].id, users[5].id]),
        }

    def test_convert_actors_to_user_set(self):
        user1 = self.create_user()
        user2 = self.create_user()
        user3 = self.create_user()
        user4 = self.create_user()

        team1 = self.create_team()
        team2 = self.create_team()

        self.create_member(user=user1, organization=self.organization, teams=[team1])
        self.create_member(user=user2, organization=self.organization, teams=[team2])
        self.create_member(user=user3, organization=self.organization, teams=[team1, team2])
        self.create_member(user=user4, organization=self.organization, teams=[])

        team1_events = set([
            self.create_event(),
            self.create_event(),
            self.create_event(),
            self.create_event(),
        ])
        team2_events = set([
            self.create_event(),
            self.create_event(),
            self.create_event(),
            self.create_event(),
        ])
        user4_events = set([self.create_event(), self.create_event()])
        events_by_actor = {
            Actor(team1.id, Team): team1_events,
            Actor(team2.id, Team): team2_events,
            Actor(user3.id, User): team1_events.union(team2_events),
            Actor(user4.id, User): user4_events,
        }
        user_by_events = {
            user1.id: team1_events,
            user2.id: team2_events,
            user3.id: team1_events.union(team2_events),
            user4.id: user4_events,
        }
        assert convert_actors_to_user_set(events_by_actor, user_by_events.keys()) == user_by_events


class GetPersonalizedDigestsTestCase(TestCase):
    def setUp(self):
        self.user1 = self.create_user()
        self.user2 = self.create_user()
        self.user3 = self.create_user()
        self.user4 = self.create_user()

        self.team1 = self.create_team()
        self.team2 = self.create_team()
        self.team3 = self.create_team()

        self.project = self.create_project(teams=[self.team1, self.team2, self.team3])

        self.create_member(user=self.user1, organization=self.organization, teams=[self.team1])
        self.create_member(user=self.user2, organization=self.organization, teams=[self.team2])
        self.create_member(
            user=self.user3,
            organization=self.organization,
            teams=[
                self.team1,
                self.team2])
        self.create_member(user=self.user4, organization=self.organization, teams=[self.team3])

        start_time = timezone.now()

        self.team1_events = self.create_events(
            start_time, self.project, [
                'hello.py', 'goodbye.py', 'hola.py', 'adios.py'])
        self.team2_events = self.create_events(
            start_time, self.project, [
                'old.cbl', 'retro.cbl', 'cool.cbl', 'gem.cbl'])

        self.user4_events = [
            self.create_event(
                group=self.create_group(
                    project=self.project), data=self.create_event_data(
                    'foo.bar', 'helloworld.org')),
            self.create_event(
                group=self.create_group(
                    project=self.project), data=self.create_event_data(
                    'bar.foo', 'helloworld.org')),
        ]
        self.team1_matcher = Matcher('path', '*.py')
        self.team2_matcher = Matcher('path', '*.cbl')
        self.user4_matcher = Matcher('url', '*.org')

        ProjectOwnership.objects.create(
            project_id=self.project.id,
            schema=dump_schema([
                Rule(self.team1_matcher, [
                    Owner('team', self.team1.slug),
                    Owner('user', self.user3.email),
                ]),
                Rule(self.team2_matcher, [
                    Owner('team', self.team2.slug),
                ]),
                Rule(self.user4_matcher, [
                    Owner('user', self.user4.email),
                ]),
            ]),
            fallthrough=True,
        )

    def create_event_data(self, filename, url='http://example.com'):
        data = {
            'tags': [('level', 'error')],
            'sentry.interfaces.Stacktrace': {
                'frames': [
                    {
                        'lineno': 1,
                        'filename': filename,
                    },
                ],
            },
            'sentry.interfaces.Http': {
                'url': url
            },
        }
        return data

    def create_events(self, start_time, project, filenames=None, urls=None):
        events = []
        for index, label in enumerate(filenames or urls):
            group = self.create_group(
                project=project,
                first_seen=start_time - timedelta(days=index + 1),
                last_seen=start_time - timedelta(hours=index + 1),
                message='group%d' % index
            )
            if filenames is not None:
                event = self.create_event(
                    group=group,
                    message=group.message,
                    datetime=group.last_seen,
                    project=project,
                    data=self.create_event_data(filename=label)
                )
            else:
                event = self.create_event(
                    group=group,
                    message=group.message,
                    datetime=group.last_seen,
                    project=project,
                    data=self.create_event_data('foo.bar', url=label)
                )
            events.append(event)
        return events

    def assert_get_personalized_digests(self, project, digest, user_ids, expected_result):
        result_user_ids = []
        for user_id, user_digest in get_personalized_digests(project.id, digest, user_ids):
            assert user_id in expected_result
            assert expected_result[user_id] == get_events_from_digest(user_digest)
            result_user_ids.append(user_id)

        assert sorted(user_ids) == sorted(result_user_ids)

    def test_build_events_by_actor(self):
        events = self.team1_events + self.team2_events + self.user4_events

        events_by_actor = {
            Actor(self.team1.id, Team): set(self.team1_events),
            Actor(self.team2.id, Team): set(self.team2_events),
            Actor(self.user3.id, User): set(self.team1_events),
            Actor(self.user4.id, User): set(self.user4_events),
        }
        assert build_events_by_actor(self.project.id, events) == events_by_actor

    def test_simple(self):
        rule = self.project.rule_set.all()[0]
        records = [event_to_record(event, (rule, ))
                   for event in self.team1_events + self.team2_events + self.user4_events]
        digest = build_digest(self.project, sort_records(records))

        expected_result = {
            self.user1.id: set(self.team1_events),
            self.user2.id: set(self.team2_events),
            self.user3.id: set(self.team1_events + self.team2_events),
            self.user4.id: set(self.user4_events),
        }
        user_ids = expected_result.keys()
        self.assert_get_personalized_digests(self.project, digest, user_ids, expected_result)

    def test_team_without_members(self):
        team = self.create_team()
        project = self.create_project(teams=[team])
        ProjectOwnership.objects.create(
            project_id=project.id,
            schema=dump_schema([
                Rule(Matcher('path', '*.cpp'), [
                    Owner('team', team.slug),
                ]),
            ]),
            fallthrough=True,
        )
        rule = project.rule_set.all()[0]
        records = [
            event_to_record(event, (rule, )) for event in self.create_events(timezone.now(), project, [
                'hello.py', 'goodbye.py', 'hola.py', 'adios.py'])
        ]
        digest = build_digest(project, sort_records(records))
        user_ids = [member.user_id for member in team.member_set]
        assert not user_ids
        for user_id, user_digest in get_personalized_digests(project.id, digest, user_ids):
            assert False  # no users in this team no digests should be processed

    def test_only_everyone(self):
        rule = self.project.rule_set.all()[0]
        events = self.create_events(
            timezone.now(), self.project, [
                'hello.moz', 'goodbye.moz', 'hola.moz', 'adios.moz'])
        records = [event_to_record(event, (rule, )) for event in events]
        digest = build_digest(self.project, sort_records(records))
        expected_result = {
            self.user1.id: set(events),
            self.user2.id: set(events),
            self.user3.id: set(events),
            self.user4.id: set(events),
        }
        user_ids = expected_result.keys()
        self.assert_get_personalized_digests(self.project, digest, user_ids, expected_result)

    def test_everyone_with_owners(self):
        rule = self.project.rule_set.all()[0]
        events = self.create_events(
            timezone.now(), self.project, [
                'hello.moz', 'goodbye.moz', 'hola.moz', 'adios.moz'])
        records = [event_to_record(event, (rule, )) for event in events + self.team1_events]
        digest = build_digest(self.project, sort_records(records))
        expected_result = {
            self.user1.id: set(events + self.team1_events),
            self.user2.id: set(events),
            self.user3.id: set(events + self.team1_events),
            self.user4.id: set(events),
        }
        user_ids = expected_result.keys()
        self.assert_get_personalized_digests(self.project, digest, user_ids, expected_result)
