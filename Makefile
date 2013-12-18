export prefix = /usr/

## can't do "thrash-protect.py --version" since it's unsupported in python versions lower than 2.7.
export version ::= $(shell grep '__version__.*=' thrash-protect.py | cut -f2 -d'"')

install: thrash-protect.py
	install "thrash-protect.py" "$(prefix)/sbin/thrash-protect"
	[ -d "$(prefix)/lib/systemd/system" ] && install systemd/thrash-protect.service "$(prefix)/lib/systemd/system"

archlinux: archlinux/PKGBUILD_ thrash-protect.py
	${MAKE} -C $@ archlinux

.PHONY: install archlinux
