export INSTALL_ROOT = "$(DESTDIR)"
export PREFIX = ${INSTALL_ROOT}/usr
export pkgname = "thrash-protect"

## can't do "thrash-protect.py --version" since it's unsupported in python versions lower than 2.7.
export version = $(shell grep '__version__.*=' thrash-protect.py | cut -f2 -d'"')

.PHONY: build install clean distclean rpm archlinux dist release ubuntu

all: build

build:
	@echo "MAKE BUILD: so far this project consists of a python prototype, no build needed"

clean:
	git clean -fd

distclean: clean

dist: distclean
	tar czf ${pkgname}-${version}.tar.gz --transform='s,^,${pkgname}-${version}/,' *

ChangeLog.recent: ChangeLog
	perl -pe 'if (/^\d\d\d\d-\d\d-\d\d/) { $$q++; exit if $$q>1; }' ChangeLog > ChangeLog.recent

install: thrash-protect.py
	install "thrash-protect.py" "$(PREFIX)/sbin/thrash-protect"
	if [ -d "$(INSTALL_ROOT)/lib/systemd/system" ]; then install systemd/thrash-protect.service "$(INSTALL_ROOT)/lib/systemd/system" ; \
        elif [ -d "$(PREFIX)/lib/systemd/system" ]; then install systemd/thrash-protect.service "$(PREFIX)/lib/systemd/system" ; fi
	if [ -d "$(INSTALL_ROOT)/etc/init" ]; then install upstart/thrash-protect.conf "$(INSTALL_ROOT)/etc/init/thrash-protect.conf" ; fi
	[ -d "$(PREFIX)/lib/systemd/system" ] || [ -d "$(INSTALL_ROOT)/etc/init" ] || [ -d "$(INSTALL_ROOT)/lib/systemd/system" ] || install systemv/thrash-protect "$(INSTALL_ROOT)/etc/init.d/thrash-protect"

.tag.${version}: ChangeLog.recent
	if ! git show --oneline -s "v${version}" > /dev/null 2>&1; then git status ; cat ChangeLog.recent ; git tag -s "v${version}" -F ChangeLog.recent ; git push origin "v${version}" ; fi
	touch ".tag.${version}"

release: .tag.${version}

archlinux: .tag.${version} archlinux/PKGBUILD_ thrash-protect.py
	${MAKE} -C $@ archlinux

rpm: .tag.${version} rpm/thrash-protect.spec thrash-protect.py
	${MAKE} -C $@ rpm

## TODO: debian target (with systemv)

## TODO: not tested
ubuntu: .tag.${version} debian/changelog
	rm -f debian/${pkgname}.init
	dpkg-buildpackage

debian: .tag.${version} debian/changelog
	dpkg-buildpackage

debian/changelog: .tag.${version}
	dch --distribution=UNRELEASED -v ${version} "version bump"

