PKGBUILD: PKGBUILD_ ../.tag.${version}
	perl -pe 's/^\#\#.*$$//; s/^pkgver=xxx$$/pkgver=${version}/;' PKGBUILD_ > PKGBUILD
	makepkg -g >> PKGBUILD

.SRCINFO: PKGBUILD
	makepkg --printsrcinfo > .SRCINFO

thrash-protect:
	git clone ssh://aur@aur.archlinux.org/thrash-protect.git

archlinux: .SRCINFO PKGBUILD thrash-protect
	cp -a .SRCINFO PKGBUILD thrash-protect
	{ cd thrash-protect ; git add PKGBUILD .SRCINFO ; git commit -m "new version ${version}" ; git push ; }
