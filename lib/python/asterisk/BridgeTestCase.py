#!/usr/bin/env python
'''
Copyright (C) 2012, Digium, Inc.
Mark Michelson <mmichelson@digium.com>

This program is free software, distributed under the terms of
the GNU General Public License Version 2.
'''

import sys
import logging
import uuid
import os

sys.path.append("lib/python")
from TestCase import TestCase

LOGGER = logging.getLogger(__name__)
class BridgeTestCase(TestCase):

    ALICE_CONNECTED = '"Bob" <4321>'
    BOB_CONNECTED = '"Alice" <1234>'

    FEATURE_MAP = {
            'blindxfer' : 1,
            'atxfer' : 2,
            'disconnect' : 3,
            'automon' : 4,
            'automixmon' : 5,
            'parkcall' : 6
            }

    def __init__(self, test_path = '', test_config = None):
        '''
        Class that handles tests involving two-party bridges.
        There are three Asterisk instances used for this test.
        0 : the unit under test "UUT"
        1 : the unit from which calls originate, also known as "Alice"
        2 : the unit where calls terminate, also known as "Bob"
        '''
        TestCase.__init__(self, test_path)
        self.create_asterisk(3, "%s/configs/bridge" % os.getcwd())
        self.test_runs = []
        self.current_run = 0
        self.ami_uut = None
        self.ami_alice = None
        self.ami_bob = None
        self.call_end_observers = []

        if test_config is None:
            LOGGER.error("No configuration provided. Bailing.")
            raise Exception

        # Just a quick sanity check so we can die early if
        # the tests are badly misconfigured
        for test_run in test_config:
            if not 'originate_channel' in test_run:
                LOGGER.error("No configured originate channel in test run")
                raise Exception

            self.test_runs.append(test_run)

        LOGGER.info("Bridge test initialized")

    def run(self):
        TestCase.run(self)
        self.create_ami_factory(3)

    def ami_connect(self, ami):
        self.ast[ami.id].cli_exec("sip set debug on")
        self.ast[ami.id].cli_exec("iax2 set debug on")
        self.ast[ami.id].cli_exec("xmpp set debug on")
        if (ami.id == 0):
            self.ami_uut = ami
            self.ami_uut.registerEvent('Bridge', self.uut_bridge_callback)
            self.ami_uut.registerEvent('TestEvent', self.test_callback)
            self.ami_uut.registerEvent('Hangup', self.hangup_callback)
            LOGGER.info("UUT AMI connected")
        elif (ami.id == 1):
            self.ami_alice = ami
            self.ami_alice.registerEvent('UserEvent', self.user_callback)
            self.ami_alice.registerEvent('Hangup', self.hangup_callback)
            LOGGER.info("Alice AMI connected")
        elif (ami.id == 2):
            self.ami_bob = ami
            self.ami_bob.registerEvent('UserEvent', self.user_callback)
            self.ami_bob.registerEvent('Hangup', self.hangup_callback)
            LOGGER.info("Bob AMI connected")
        else:
            LOGGER.warning("Unexpected AMI ID %d recieved" % ami.id)

        if self.ami_uut and self.ami_alice and self.ami_bob:
            # We can get started with the test!
            LOGGER.info("Time to run tests!")
            self.run_tests()

    def run_tests(self):
        if self.current_run < len(self.test_runs):
            self.reset_timeout()
            self.start_test(self.test_runs[self.current_run])
        else:
            LOGGER.info("All calls executed, stopping")
            self.set_passed(True)
            self.stop_reactor()

    def start_test(self, test_run):
        # Step 0: Set up event handlers and initialize values for this test run
        self.hangup = test_run['hangup'] if 'hangup' in test_run else None
        self.features = test_run['features'] if 'features' in test_run else []
        self.alice_channel = None
        self.bob_channel = None
        self.uut_alice_channel = None
        self.uut_bob_channel = None
        self.alice_hungup = False
        self.bob_hungup = False
        self.uut_alice_hungup = False
        self.uut_bob_hungup = False
        self.current_feature = 0
        self.infeatures = False
        self.issue_hangups_on_bridged = False

        # Step 1: Initiate a call from Alice to Bob
        LOGGER.info("Originating call")
        self.ami_alice.originate(channel = test_run['originate_channel'],
                exten = 'test_call',
                context = 'default',
                priority = '1',
                variable = {'TALK_AUDIO' : '%s' % os.path.join(os.getcwd(),
                        'lib/python/asterisk/audio')})

    def user_callback(self, ami, event):
        if (event.get('userevent') == 'Connected'):
            if ami is self.ami_bob:
                self.bob_channel = event.get('channel')
                LOGGER.info("Bob's channel is %s" % self.bob_channel)
            elif ami is self.ami_alice:
                self.alice_channel = event.get('channel')
                LOGGER.info("Alice's channel is %s" % self.alice_channel)

        if (event.get('userevent') == 'TalkDetect'):
            if event.get('result') == 'pass':
                LOGGER.info("Two way audio properly detected between Bob and Alice")
                self.audio_detected = True
                self.check_identities()
            else:
                LOGGER.warning("Audio issues on bridged call")
                self.stop_reactor()

    def hangup_callback(self, ami, event):
        if ami is self.ami_bob:
            LOGGER.info("Bob has hung up")
            self.bob_hungup = True
        elif ami is self.ami_alice:
            LOGGER.info("Alice has hung up")
            self.alice_hungup = True
        elif ami is self.ami_uut:
            if event.get('channel') == self.uut_alice_channel:
                LOGGER.info("UUT Alice hang up")
                self.uut_alice_hungup = True
            elif event.get('channel') == self.uut_bob_channel:
                LOGGER.info("UUT Bob hang up")
                self.uut_bob_hungup = True

        if (self.bob_hungup and self.alice_hungup and self.uut_alice_hungup and
                self.uut_bob_hungup):
            for callback in self.call_end_observers:
                callback(self.ami_uut, self.ami_alice, self.ami_alice)
            # Test call has concluded move on!
            self.current_run += 1
            self.run_tests()

    def uut_bridge_callback(self, ami, event):
        LOGGER.debug("Got bridge callback")
        self.uut_alice_channel = event.get('channel1')
        self.uut_bob_channel = event.get('channel2')
        if event.get('bridgestate') == 'Link':
            LOGGER.debug("Bridge is up")
            LOGGER.debug("Type of bridge is %s" % event.get('bridgetype'))
            self.bridged = True
            if self.issue_hangups_on_bridged:
                self.send_hangup()
        else:
            LOGGER.debug("Bridge is down")
            self.bridged = False

    def check_identities(self):
        def alice_connected(value):
            LOGGER.info("Alice's Connected line is %s" % value)
            if value != BridgeTestCase.ALICE_CONNECTED:
                LOGGER.warning("Unexpected Connected line value for Alice: %s" %
                        value)
                self.set_passed(False)

        def bob_connected(value):
            LOGGER.info("Bob's Connected line is %s" % value)
            if value != BridgeTestCase.BOB_CONNECTED:
                LOGGER.warning("Unexpected Connected line value for Bob: %s" %
                        value)
                self.set_passed(False)

        def alice_bridgepeer(value):
            LOGGER.info("Alice's BRIDGEPEER is %s" % value)
            if value != self.uut_bob_channel:
                LOGGER.warning("Unexpected BRIDGEPEER value for Alice: %s" %
                        value)
                self.set_passed(False)

        def bob_bridgepeer(value):
            LOGGER.info("Bob's BRIDGEPEER is %s" % value)
            if value != self.uut_alice_channel:
                LOGGER.warning("Unexpected BRIDGEPEER value for Bob: %s" %
                        value)
                self.set_passed(False)
            self.execute_features()

        self.ami_uut.getVar(self.uut_alice_channel,
                'CONNECTEDLINE(all)').addCallback(alice_connected)
        bob_connected = self.ami_uut.getVar(self.uut_bob_channel,
                'CONNECTEDLINE(all)').addCallback(bob_connected)
        alice_bridgepeer = self.ami_uut.getVar(self.uut_alice_channel,
                'BRIDGEPEER').addCallback(alice_bridgepeer)
        bob_bridgepeer = self.ami_uut.getVar(self.uut_bob_channel,
                'BRIDGEPEER').addCallback(bob_bridgepeer)

    def execute_features(self):
        if self.current_feature < len(self.features):
            self.infeatures = True
            self.reset_timeout()
            LOGGER.info("Going to execute a feature")
            self.execute_feature(self.features[self.current_feature])
        else:
            LOGGER.info("All features executed")
            self.send_hangup()

    def execute_feature(self, feature):
        if not 'who' in feature or not 'what' in feature or not 'success' in feature:
            LOGGER.warning("Missing necessary feature information")
            self.set_passed(False)
        if feature['who'] == 'alice':
            ami = self.ami_alice
            channel = self.alice_channel
        elif feature['who'] == 'bob':
            ami = self.ami_bob
            channel = self.bob_channel
        else:
            LOGGER.warning("Feature target must be 'alice' or 'bob'")
            self.set_passed(False)

        if feature['what'] not in BridgeTestCase.FEATURE_MAP:
            LOGGER.warning("Unknown feature requested")
            self.set_passed(False)

        if feature['success'] == 'true':
            self.feature_success = True
        else:
            self.feature_success = False

        LOGGER.info("Sending feature %s from %s" % (feature['what'],
            feature['who']))
        ami.playDTMF(channel, BridgeTestCase.FEATURE_MAP[feature['what']])

    def test_callback(self, ami, event):
        if event.get('state') != 'FEATURE_DETECTION':
            return

        if not self.infeatures:
            # We don't care about features yet, so
            # just return
            return

        LOGGER.info("Got FEATURE_DETECTION event")
        if event.get('result') == 'success':
            LOGGER.info("Feature detected was %s" % event.get('feature'))
            if not self.feature_success:
                LOGGER.warning("Feature succeeded when failure expected")
                self.set_passed(False)
            elif (self.features[self.current_feature]['what'] !=
                    event.get('feature')):
                LOGGER.warning("Unexpected feature triggered")
                self.set_passed(False)
        else:
            LOGGER.info("No feature detected")
            if self.feature_success:
                LOGGER.warning("Feature failed when success was expected")
                self.set_passed(False)
        # Move onto the next feature!
        self.current_feature += 1
        self.execute_features()

    def send_hangup(self):
        if not self.hangup:
            LOGGER.info("No hangup set. Hang up will happen externally")
            return

        if not self.bridged:
            LOGGER.info("Delaying hangup until call is re-bridged")
            self.issue_hangups_on_bridged = True
            return

        LOGGER.info("Sending a hangup to %s" % self.hangup)
        if self.hangup == 'alice':
            ami = self.ami_alice
            channel = self.alice_channel
        elif self.hangup == 'bob':
            ami = self.ami_bob
            channel = self.bob_channel
        else:
            raise Exception("Invalid hangup target specified: %s" % self.hangup)

        ami.hangup(channel)

    def register_call_end_observer(self, callback):
        self.call_end_observers.append(callback)
