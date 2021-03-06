#!/bin/bash

set +e

testdir=${0%%run-test}
ASTERISK=/usr/sbin/asterisk
debug=0
gotversion=0

usage() {
	echo "Usage: $0 -v asterisk-<version> [-d]"
	exit 1
}

case `uname -s` in
	solaris*|sunos*)
		PS="ps -ef"
		KILLALL="pkill"
		;;
	*)
		PS="ps auxwww"
		KILLALL="killall"
		;;
esac

while test "$1" != ""; do
	case $1 in
		-v)
			shift
			case $1 in
				*-1.4*)
					ORIGINATE='originate'
					CSC_HEADERS=3
					AGI_NOTFOUND=FAILURE
					;;
				*-1.6.2*)
					ORIGINATE='originate'
					CSC_HEADERS=4
					AGI_NOTFOUND=NOTFOUND
					;;
				"")
					usage
					;;
				*)
					ORIGINATE='channel originate'
					CSC_HEADERS=4
					AGI_NOTFOUND=NOTFOUND
					;;
			esac
			gotversion=1
			;;
		-d)
			let debug++
			;;
		*)
			usage
			;;
	esac
	shift
done

if test "$gotversion" = "0"; then
	usage
fi

#
# initialize():
#   Parameters:  directories within the test to use for each instance of Asterisk needed in this test
#
initialize() {
	# Save all initialized directories
	lib_SYSDIRS=$@

	# Local variables
	local dst=""
	local conf=""
	local user=""
	local i=""

	for user in $@; do
		echo " >>> Creating configuration directory for $user"
		eval "${user}_tmpdir=`mktemp -d /var/tmp/testsuite_$user.XXXXXX`"
		# Shortcut for referral within this loop only
		eval "conf=\${${user}_tmpdir}"

		if test "$conf" = ""; then
			echo " *** Unable to create temporary directory for $user"
			cleanup
			exit 1
		fi

		echo " >>> Creating files in configuration directory for $user"
		for i in ../configs/*.sample; do
			dst=$conf/`basename $i .sample`
			install -m 644 $i $dst
		done
		for i in $testdir$user/*.conf; do
			dst=$conf/`basename $i`
			install -m 644 $i $dst
		done
		( echo "[directories]"; echo "astetcdir => $conf"; echo "astrundir => $conf"; echo "astlogdir => $testdir/$user"; echo "astspooldir => $conf/spool" ; echo "astagidir => $testdir/$user/agi-bin" ; echo "[options]"; echo "verbose = 10"; echo "documentation_language = en_US" ) > $conf/asterisk.conf
		( echo "noload => pbx_lua.so" ; echo "noload => pbx_ael.so" ) >> $conf/modules.conf
		mkdir -p $conf/spool/outgoing

		# Verify that files got created properly
		if test -s $conf/asterisk.conf ; then : ; else
			echo " *** Unable to create test configuration files for $user in $conf"
			cleanup
			exit 1
		fi
		echo " >>> Starting Asterisk for $user"
		if test $debug -gt 0; then
			debug_flags="-vvvddd"
		fi
		$ASTERISK -g -C $conf/asterisk.conf
		# Asterisk is running, right?
		if $ASTERISK $debug_flags -C $conf/asterisk.conf -rx "core waitfullybooted" >/dev/null 2>&1; then :; else
			echo " *** Unable to start asterisk for $user"
			cleanup
			exit 1
		fi
	done
}

#
# cleanup()
#   Parameters: none
#
cleanup() {
	# Hide local exit values from other functions and the parent script
	for a in $lib_SYSDIRS; do
		eval "local $a=0"
	done
	local conf=""
	local u=""
	local avals=""
	local bvals=""

	echo -n "Shutting test instances down"

	for u in $lib_SYSDIRS; do
		eval "conf=\${${u}_tmpdir}"
		if test "x$conf" = "x"; then : ; else
			$ASTERISK -C $conf/asterisk.conf -rx "core stop when convenient" >/dev/null 2>&1 &
			echo -n " ."
		fi
	done
	sleep 2
	for u in $lib_SYSDIRS; do
		eval "conf=\${${u}_tmpdir}"
		if test "x$conf" = "x"; then : ; else
			$ASTERISK -C $conf/asterisk.conf -rx "core stop now" >/dev/null 2>&1
			let $u=$?
			echo -n " ."
		fi
	done
	avals=""
	bvals=""
	for u in $lib_SYSDIRS; do
		avals="1$avals"
		eval "bvals=${bvals}\${$u}"
	done
	if test "$avals" != "$bvals"; then
		sleep 2
		for u in $lib_SYSDIRS; do
			eval "conf=\${${u}_tmpdir}"
			if test "x$conf" = "x"; then : ; else
				$ASTERISK -C $conf/asterisk.conf -rx "core stop now" >/dev/null 2>&1
				let $u=$?
				echo -n " ."
			fi
		done
		avals=""
		bvals=""
		for u in $lib_SYSDIRS; do
			avals="1$avals"
			eval "bvals=${bvals}\${$u}"
		done
		if test "$avals" != "$bvals"; then
			$KILLALL asterisk >/dev/null 2>&1
			for u in $lib_SYSDIRS; do
				eval "conf=\${${u}_tmpdir}"
				# This is a "nonsense" command.  If Asterisk is not running, it will exit non-zero.
				$ASTERISK -C $conf/asterisk.conf -rx "core set verbose atleast 1" >/dev/null 2>&1
				let $u=$?
				echo -n " ."
			done
		fi
	fi
	avals=""
	bvals=""
	for u in $lib_SYSDIRS; do
		avals="1$avals"
		eval "bvals=${bvals}\${$u}"
	done
	if test "$avals" != "$bvals"; then
		if test $debug -gt 0; then
			echo -n " A:'$avals' B:'$bvals'"
		fi
		$KILLALL -9 asterisk >/dev/null 2>&1
		echo -n " (forcefully) ."
	fi

	echo " done!"

	echo -n "Removing temporary directories"

	for u in $lib_SYSDIRS; do
		eval "rm -rf \${${u}_tmpdir}"
		echo -n " ."
	done
	echo " done!"

	set -e
}

#
# verify_call()
#   Parameters:
#     1. Asterisk instance
#     2. Number of channels expected to be up
#
verify_call() {
	# Verify that the call is up
	if test -d $1; then
		conf=$1
	else
		eval "conf=\${${1}_tmpdir}"
	fi
	local count=`$ASTERISK -C $conf/asterisk.conf -rx "core show channels" | wc -l`
	if test $count != $(($CSC_HEADERS+$2)); then
		echo -n " *** Call generation failed: got $count, but expected "
		echo $(($CSC_HEADERS+$2))
		if test "$debug" = "1"; then
			echo ">>>>>$1>>>>>"
			$ASTERISK -C $conf/asterisk.conf -rx "core show channels"
			echo "<<<<<$1<<<<<"
		fi
		cleanup
		exit 1
	fi
}

