#!/usr/bin/env python
'''Asterisk external test suite driver.

Copyright (C) 2011, Digium, Inc.
Russell Bryant <russell@digium.com>
Matt Jordan    <mjordan@digium.com>

This program is free software, distributed under the terms of
the GNU General Public License Version 2.
'''

import sys
import os
import subprocess
import optparse
import time
import yaml
import socket

sys.path.append("lib/python")

import utils
from version import AsteriskVersion
from asterisk import Asterisk
from buildoptions import AsteriskBuildOptions
from sippversion import SIPpVersion

class TestConditionConfig:
    """
    This class creates a test condition config and will build up an
    object that derives from TestCondition based on that configuration
    """

    def __init__(self, config):
        """
        Construct a new test condition from a config sequence constructed
        from the test-config.yaml file
        """
        self.classTypeName = ""
        self.passExpected = True
        self.type = ""
        self.relatedCondition = ""
        if 'name' in config:
            self.classTypeName = config['name']
        if 'expectedResult' in config:
            try:
                self.passExpected = not (config["expectedResult"].upper().strip() == "FAIL")
            except:
                self.passExpected = False
                print "ERROR: '%s' is not a valid value for expectedResult" % config["expectedResult"]
        if 'type' in config:
            self.type = config['type'].upper().strip()
        if 'relatedCondition' in config:
            self.relatedCondition = config['relatedCondition'].strip()
        """ Let non-standard configuration items be obtained from the config object """
        self.config = config

    def get_type(self):
        """
        The type of TestCondition (either PRE or POST)
        """
        return self.type

    def get_related_condition(self):
        """
        The type name of the related condition that this condition will use
        """
        return self.relatedCondition

    def make_condition(self):
        """
        Build up and return the condition object defined by this configuration
        """
        parts = self.classTypeName.split('.')
        module = ".".join(parts[:-1])
        if module != "":
            m = __import__(module)
            for comp in parts[1:]:
                m = getattr(m, comp)
            obj = m(self)
            return obj
        return None


class Dependency:
    """
    This class checks and stores the dependencies for a particular Test.
    """

    __asteriskBuildOptions = AsteriskBuildOptions()

    __ast = Asterisk()

    def __init__(self, dep):
        """
        Construct a new dependency

        Keyword arguments:
        dep -- A tuple containing the dependency type name and its subinformation.
        """

        self.name = ""
        self.version = ""
        self.met = False
        if "app" in dep:
            self.name = dep["app"]
            self.met = utils.which(self.name) is not None
        elif "python" in dep:
            self.name = dep["python"]
            try:
                __import__(self.name)
                self.met = True
            except ImportError:
                pass
        elif "sipp" in dep:
            self.name = "SIPp"
            version = None
            feature = None
            if 'version' in dep['sipp']:
                version = dep['sipp']['version']
            if 'feature' in dep['sipp']:
                feature = dep['sipp']['feature']
            self.sipp_version = SIPpVersion()
            self.version = SIPpVersion(version, feature)
            if self.sipp_version >= self.version:
                self.met = True
            if self.version.tls and not self.sipp_version.tls:
                self.met = False
            if self.version.pcap and not self.sipp_version.pcap:
                self.met = False
        elif "custom" in dep:
            self.name = dep["custom"]
            method = "depend_%s" % self.name
            found = False
            for m in dir(self):
                if m == method:
                    self.met = getattr(self, m)()
                    found = True
            if not found:
                print "Unknown custom dependency - '%s'" % self.name
        elif "asterisk" in dep:
            self.name = dep["asterisk"]
            self.met = self.__find_asterisk_module(self.name)
        elif "buildoption" in dep:
            self.name = dep["buildoption"]
            self.met = self.__find_build_flag(self.name)
        else:
            print "Unknown dependency type specified:"
            for key in dep.keys():
                print key

    def depend_soundcard(self):
        """
        Check the soundcard custom dependency
        """
        try:
            f = open("/dev/dsp", "r")
            f.close()
            return True
        except:
            return False

    def depend_ipv6(self):
        """
        Check the ipv6 custom dependency
        """
        try:
            s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            s.close()
            return True
        except:
            return False

    def depend_pjsuav6(self):
        '''
        This tests if pjsua was compiled with IPv6 support. To do this,
        we run pjsua --help and parse the output to determine if --ipv6
        is a valid option
        '''
        if utils.which('pjsua') is None:
            return False

        help_output = subprocess.Popen(['pjsua', '--help'],
                                       stdout=subprocess.PIPE).communicate()[0]
        if help_output.find('--ipv6') == -1:
            return False
        return True

    def depend_fax(self):
        """
        Checks if Asterisk contains the necessary modules for fax tests
        """
        fax_providers = [
            "app_fax",
            "res_fax_spandsp",
            "res_fax_digium",
        ]

        for f in fax_providers:
            if self.__find_asterisk_module(f):
                return True

        return False

    def __find_build_flag(self, name):
        return (self.__asteriskBuildOptions.checkOption(name))

    def __find_asterisk_module(self, name):
        if "astmoddir" not in Dependency.__ast.directories:
            return False

        module = "%s/%s.so" % (Dependency.__ast.directories["astmoddir"], name)
        if os.path.exists(module):
            return True

        return False


class TestConfig:
    """
    Class that contains the configuration for a specific test, as parsed
    by that tests test.yaml file.
    """

    def __init__(self, test_name):
        """
        Create a new TestConfig

        Keyword arguments:
        test_name -- The path to the directory containing the test-config.yaml file to load
        """
        self.can_run = True
        self.test_name = test_name
        self.skip = None
        self.config = None
        self.summary = None
        self.maxversion = None
        self.maxversion_check = False
        self.minversion = None
        self.minversion_check = False
        self.deps = []
        self.expectPass = True

        self.__parse_config()

    def __process_testinfo(self):
        self.summary = "(none)"
        self.description = "(none)"
        if "testinfo" not in self.config:
            return
        testinfo = self.config["testinfo"]
        if "skip" in testinfo:
            self.skip = testinfo['skip']
            self.can_run = False
        if "summary" in testinfo:
            self.summary = testinfo["summary"]
        if "description" in testinfo:
            self.description = testinfo["description"]

    def __process_properties(self):
        self.minversion = AsteriskVersion("1.4")
        if "properties" not in self.config:
            return
        properties = self.config["properties"]
        if "minversion" in properties:
            try:
                self.minversion = AsteriskVersion(properties["minversion"])
            except:
                self.can_run = False
                print "ERROR: '%s' is not a valid minversion" % \
                        properties["minversion"]
        if "maxversion" in properties:
            try:
                self.maxversion = AsteriskVersion(properties["maxversion"])
            except:
                self.can_run = False
                print "ERROR: '%s' is not a valid maxversion" % \
                        properties["maxversion"]
        if "expectedResult" in properties:
            try:
                self.expectPass = not (properties["expectedResult"].upper().strip() == "FAIL")
            except:
                self.can_run = False
                print "ERROR: '%s' is not a valid value for expectedResult" %\
                        properties["expectedResult"]

    def __parse_config(self):
        test_config = "%s/test-config.yaml" % self.test_name
        try:
            f = open(test_config, "r")
        except IOError:
            print "Failed to open %s" % test_config
            return
        except:
            print "Unexpected error: %s" % sys.exc_info()[0]
            return

        self.config = yaml.load(f)
        f.close()

        if not self.config:
            print "ERROR: Failed to load configuration for test '%s'" % \
                    self.test_name
            return

        self.__process_testinfo()
        self.__process_properties()

    def get_conditions(self):
        """
        Gets the pre- and post-test conditions associated with this test

        Returns a list of 3-tuples containing the following:
            0: The actual condition object
            1: The condition type (either PRE or POST)
            2: The name of the related condition that this one depends on
        """
        conditions = []
        if not self.config:
            return conditions

        conditionsTemp = [TestConditionConfig(c) for c in self.config["properties"].get("testconditions") or [] ]

        for cond in conditionsTemp:
            c = cond.make_condition(), cond.get_type(), cond.get_related_condition()
            conditions.append(c)

        return conditions

    def check_deps(self, ast_version):
        """
        Check whether or not a test should execute based on its configured dependencies

        Keyword arguments:
        ast_version -- The AsteriskVersion object containing the version of Asterisk that
            will be executed
        returns can_run - True if the test can execute, False otherwise
        """

        if not self.config:
            return False

        self.deps = [
            Dependency(d)
                for d in self.config["properties"].get("dependencies") or []
        ]

        self.minversion_check = True
        if ast_version < self.minversion:
            self.can_run = False
            self.minversion_check = False
            return self.can_run

        self.maxversion_check = True
        if self.maxversion is not None and ast_version > self.maxversion:
            self.can_run = False
            self.maxversion_check = False
            return self.can_run

        for d in self.deps:
            if d.met is False:
                self.can_run = False
                break
        return self.can_run