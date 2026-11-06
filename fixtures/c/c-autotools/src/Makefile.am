AM_CPPFLAGS = -I$(top_srcdir)/include
AM_CFLAGS = -std=c99 -Wall

lib_LTLIBRARIES = libkvconf.la
libkvconf_la_SOURCES = strutil.c value.c log.c doc.c parse.c kvint.h
libkvconf_la_LDFLAGS = -version-info 3:1:0
