# wordfreq - word frequency counting library and program (C89).

CC = cc
AR = ar
CFLAGS = -O2 -ansi -pedantic -Wall -Iinclude -Isrc

OBJS = src/sbuf.o src/htab.o src/tok.o src/conf.o src/wf.o
TESTS = tests/test_sbuf tests/test_htab tests/test_tok tests/test_wf

all: libwf.a wordfreq

.c.o:
	$(CC) $(CFLAGS) -c $< -o $@

libwf.a: $(OBJS)
	rm -f libwf.a
	$(AR) cq libwf.a $(OBJS)

wordfreq: tools/wordfreq.o libwf.a
	$(CC) $(CFLAGS) -o wordfreq tools/wordfreq.o -L. -lwf

tests/test_sbuf: tests/test_sbuf.o libwf.a
	$(CC) $(CFLAGS) -o tests/test_sbuf tests/test_sbuf.o -L. -lwf

tests/test_htab: tests/test_htab.o libwf.a
	$(CC) $(CFLAGS) -o tests/test_htab tests/test_htab.o -L. -lwf

tests/test_tok: tests/test_tok.o libwf.a
	$(CC) $(CFLAGS) -o tests/test_tok tests/test_tok.o -L. -lwf

tests/test_wf: tests/test_wf.o libwf.a
	$(CC) $(CFLAGS) -o tests/test_wf tests/test_wf.o -L. -lwf

test: $(TESTS) wordfreq
	./tests/test_sbuf
	./tests/test_htab
	./tests/test_tok
	./tests/test_wf
	./wordfreq 3 < tests/sample.txt > tests/sample.got
	cmp tests/sample.got tests/sample.out
	./wordfreq -k -m 4 2 < tests/sample.txt > tests/sample-k.got
	cmp tests/sample-k.got tests/sample-k.out

clean:
	rm -f $(OBJS) libwf.a wordfreq tools/wordfreq.o tests/*.o tests/*.got $(TESTS)

src/sbuf.o src/htab.o src/tok.o src/conf.o src/wf.o: include/wf.h src/wfint.h
tools/wordfreq.o: include/wf.h
tests/test_sbuf.o tests/test_htab.o tests/test_tok.o: include/wf.h src/wfint.h tests/tap.h
tests/test_wf.o: include/wf.h tests/tap.h
