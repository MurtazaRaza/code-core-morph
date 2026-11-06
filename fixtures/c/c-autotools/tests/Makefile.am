AM_CPPFLAGS = -I$(top_srcdir)/include
AM_CFLAGS = -std=c99 -Wall
LDADD = $(top_builddir)/src/libkvconf.la

check_PROGRAMS = test_value test_log test_doc test_parse
TESTS = $(check_PROGRAMS) dump.sh
TEST_EXTENSIONS = .sh
SH_LOG_COMPILER = $(SHELL)

EXTRA_DIST = check.h dump.sh sample.conf sample.out sample.warn sample-server.out \
	broken.conf broken.warn
CLEANFILES = dump-*.got
