export INSTALL_ROOT = "$(DESTDIR)"
export PREFIX = ${INSTALL_ROOT}/usr
export pkgname = thrash-protect

# Auto-detect version from latest .tag.* file (created by make release)
ifndef version
_detected_version := $(shell ls .tag.* 2>/dev/null | sed 's/.*\.tag\.v\{0,1\}//' | sort -V | tail -1)
ifneq ($(_detected_version),)
version := $(_detected_version)
endif
endif
export version

.PHONY: build install clean distclean rpm archlinux dist release do-release ubuntu debian

all: build

build:
	@echo "MAKE BUILD: so far this project consists of a python prototype, no build needed"

clean:
	git clean -fd

distclean: clean

dist:
ifndef version
	$(error dist requires version=X.Y.Z)
endif
	tar czf ${pkgname}-${version}.tar.gz --transform='s,^,${pkgname}-${version}/,' --exclude='${pkgname}-*.tar.gz' *

install: thrash_protect.py
	install "thrash_protect.py" "$(PREFIX)/sbin/thrash-protect"
	if [ -d "$(INSTALL_ROOT)/lib/systemd/system" ]; then install systemd/thrash-protect.service "$(INSTALL_ROOT)/lib/systemd/system" ; \
        elif [ -d "$(PREFIX)/lib/systemd/system" ]; then install systemd/thrash-protect.service "$(PREFIX)/lib/systemd/system" ; fi
	if [ -d "$(INSTALL_ROOT)/etc/init" ]; then install upstart/thrash-protect.conf "$(INSTALL_ROOT)/etc/init/thrash-protect.conf" ; fi
	if [ -x "$(INSTALL_ROOT)/sbin/openrc-run" ] ; then install openrc/thrash-protect "$(INSTALL_ROOT)/etc/init.d/thrash-protect" ; fi
	[ -d "$(PREFIX)/lib/systemd/system" ] || [ -d "$(INSTALL_ROOT)/etc/init" ] || [ -d "$(INSTALL_ROOT)/lib/systemd/system" ] || [ -x "$(INSTALL_ROOT)/sbin/openrc-run" ] || install systemv/thrash-protect "$(INSTALL_ROOT)/etc/init.d/thrash-protect"

## Interactive release: prompts for version, shows changelog, creates signed tag
release:
	git push
	@echo "=== Unreleased changes (from CHANGELOG.md) ===" && \
	sed -n '/^## \[Unreleased\]/,/^## \[/p' CHANGELOG.md | head -n -1 && \
	echo "=============================================="
	@latest=$$(git tag -l 'v[0-9]*.[0-9]*.[0-9]*' | sort -V | tail -1) && \
	latest=$${latest#v} && \
	if [ -n "$$latest" ]; then \
		major=$$(echo $$latest | cut -d. -f1) && \
		minor=$$(echo $$latest | cut -d. -f2) && \
		patch=$$(echo $$latest | cut -d. -f3) && \
		suggested="$$major.$$minor.$$((patch + 1))"; \
	else \
		suggested="0.1.0"; \
	fi && \
	read -p "Enter version to release [$$suggested]: " ver && \
	ver=$${ver:-$$suggested} && \
	ver=$${ver#v} && \
	if [ -z "$$ver" ]; then echo "Error: version required"; exit 1; fi && \
	if git show --oneline -s "v$$ver" > /dev/null 2>&1; then \
		echo "Tag v$$ver already exists"; \
	else \
		git status && \
		read -p "Create and push tag v$$ver? [y/N] " confirm && \
		if [ "$$confirm" = "y" ] || [ "$$confirm" = "Y" ]; then \
			git tag -s "v$$ver" -m "Release v$$ver - see CHANGELOG.md for details" && \
			git push origin "v$$ver" && \
			touch ".tag.$$ver" && \
			echo "Released v$$ver" && \
			notes=$$(sed -n '/^## \['"$$ver"'\]/,/^## \[/{/^## \['"$$ver"'\]/d;/^## \[/d;p;}' CHANGELOG.md) && \
			gh release create "v$$ver" --title "v$$ver" --notes "$$notes"; \
		else \
			echo "Aborted"; \
		fi \
	fi

## Package targets use auto-detected version from .tag.* files,
## or explicit version=X.Y.Z on command line.

archlinux: archlinux/PKGBUILD_ thrash_protect.py
ifndef version
	$(error archlinux requires version=X.Y.Z)
endif
	@test -f .tag.${version} || { echo "Error: Run 'make release' first to create tag v${version}"; exit 1; }
	${MAKE} -C $@ archlinux

rpm: rpm/thrash-protect.spec thrash_protect.py
ifndef version
	$(error rpm requires version=X.Y.Z)
endif
	@test -f .tag.${version} || { echo "Error: Run 'make release' first to create tag v${version}"; exit 1; }
	$(MAKE) dist version=${version}
	rsync ${pkgname}-${version}.tar.gz ${HOME}/rpmbuild/SOURCES/v${version}.tar.gz
	${MAKE} -C $@ rpm version=${version}

## TODO: debian target (with systemv)

## TODO: not tested
ubuntu:
ifndef version
	$(error ubuntu requires version=X.Y.Z)
endif
	@test -f .tag.${version} || { echo "Error: Run 'make release' first to create tag v${version}"; exit 1; }
	rm -f debian/${pkgname}.init
	dpkg-buildpackage

debian:
ifndef version
	$(error debian requires version=X.Y.Z)
endif
	@test -f .tag.${version} || { echo "Error: Run 'make release' first to create tag v${version}"; exit 1; }
	rm -rf debian/tmp
	mkdir -p debian/tmp/DEBIAN
	mkdir -p debian/tmp/usr/sbin
	mkdir -p debian/tmp/usr/lib/systemd/system
	mkdir -p debian/tmp/usr/share/doc/${pkgname}
	install -m 755 thrash_protect.py debian/tmp/usr/sbin/thrash-protect
	install -m 644 systemd/thrash-protect.service debian/tmp/usr/lib/systemd/system/
	install -m 644 README.rst debian/tmp/usr/share/doc/${pkgname}/
	install -m 644 CHANGELOG.md debian/tmp/usr/share/doc/${pkgname}/
	echo "Package: ${pkgname}" > debian/tmp/DEBIAN/control
	echo "Version: ${version}" >> debian/tmp/DEBIAN/control
	echo "Architecture: all" >> debian/tmp/DEBIAN/control
	echo "Maintainer: Tobias Brox <tobias@redpill-linpro.com>" >> debian/tmp/DEBIAN/control
	echo "Depends: python3 (>= 3.9)" >> debian/tmp/DEBIAN/control
	echo "Section: admin" >> debian/tmp/DEBIAN/control
	echo "Priority: optional" >> debian/tmp/DEBIAN/control
	echo "Description: Simple-Stupid user-space program protecting a linux host from thrashing" >> debian/tmp/DEBIAN/control
	fakeroot dpkg-deb --build debian/tmp ../${pkgname}_${version}_all.deb
	rm -rf debian/tmp

## debian/changelog is maintained manually (dch not available on all platforms)
