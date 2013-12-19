export prefix ::= /usr/
export pkgname ::= "thrash-protect"

## can't do "thrash-protect.py --version" since it's unsupported in python versions lower than 2.7.
export version ::= $(shell grep '__version__.*=' thrash-protect.py | cut -f2 -d'"')

ChangeLog.recent: ChangeLog
	perl -pe 'if (/^\d\d\d\d-\d\d-\d\d/) { $$q++; exit if $$q>1; }' ChangeLog > ChangeLog.recent

install: thrash-protect.py
	install "thrash-protect.py" "$(prefix)/sbin/thrash-protect"
	[ -d "$(prefix)/lib/systemd/system" ] && install systemd/thrash-protect.service "$(prefix)/lib/systemd/system"

.tag.${version}: ChangeLog.recent
	git status
	cat ChangeLog.recent
	git tag -s v${version} -F ChangeLog.recent
	git push origin v${version}
	touch .tag.${version}

archlinux: .tag.${version} archlinux/PKGBUILD_ thrash-protect.py
	${MAKE} -C $@ archlinux

rpm: rpm/thrash-protect.spec thrash-protect.py
	${MAKE} -C $@ rpm

.PHONY: install
