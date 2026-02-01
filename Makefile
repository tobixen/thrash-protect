export INSTALL_ROOT = "$(DESTDIR)"
export PREFIX = ${INSTALL_ROOT}/usr
export pkgname = thrash-protect

.PHONY: build install clean distclean rpm archlinux dist release do-release ubuntu

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
	tar czf ${pkgname}-${version}.tar.gz --transform='s,^,${pkgname}-${version}/,' *

install: thrash-protect.py
	install "thrash-protect.py" "$(PREFIX)/sbin/thrash-protect"
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
			echo "Released v$$ver"; \
		else \
			echo "Aborted"; \
		fi \
	fi

## Package targets require version=X.Y.Z on command line
## Example: make archlinux version=0.15.0

archlinux: archlinux/PKGBUILD_ thrash-protect.py
ifndef version
	$(error archlinux requires version=X.Y.Z)
endif
	@test -f .tag.${version} || { echo "Error: Run 'make release' first to create tag v${version}"; exit 1; }
	${MAKE} -C $@ archlinux

rpm: rpm/thrash-protect.spec thrash-protect.py
ifndef version
	$(error rpm requires version=X.Y.Z)
endif
	@test -f .tag.${version} || { echo "Error: Run 'make release' first to create tag v${version}"; exit 1; }
	$(MAKE) dist version=${version}
	rsync ${pkgname}-${version}.tar.gz ${HOME}/rpmbuild/SOURCES/v${version}.tar.gz
	${MAKE} -C $@ rpm

## TODO: debian target (with systemv)

## TODO: not tested
ubuntu: debian/changelog
ifndef version
	$(error ubuntu requires version=X.Y.Z)
endif
	@test -f .tag.${version} || { echo "Error: Run 'make release' first to create tag v${version}"; exit 1; }
	rm -f debian/${pkgname}.init
	dpkg-buildpackage

debian: debian/changelog
ifndef version
	$(error debian requires version=X.Y.Z)
endif
	@test -f .tag.${version} || { echo "Error: Run 'make release' first to create tag v${version}"; exit 1; }
	dpkg-buildpackage

debian/changelog:
ifndef version
	$(error debian/changelog requires version=X.Y.Z)
endif
	dch --distribution=UNRELEASED -v ${version} "version bump"
