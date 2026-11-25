# Module PAM de Sentinel.
#   make            compile pam_sentinel.so (nécessite libpam0g-dev)
#   make harness    programme de test, qui charge le module sans toucher à /etc/pam.d
CC ?= gcc
CFLAGS ?= -O2 -g
CFLAGS += -Wall -Wextra -Werror -fPIC -fstack-protector-strong -D_FORTIFY_SOURCE=2 $(EXTRA_CFLAGS)
LDFLAGS += -Wl,-z,relro,-z,now
PAMLIB ?= -lpam

all: pam_sentinel.so

pam_sentinel.so: pam_sentinel.c
	$(CC) $(CFLAGS) -shared -o $@ $< $(LDFLAGS) $(PAMLIB)

harness: pam_harness.c
	$(CC) $(CFLAGS) -o pam-harness $< $(PAMLIB)

clean:
	rm -f pam_sentinel.so pam-harness

.PHONY: all clean harness
