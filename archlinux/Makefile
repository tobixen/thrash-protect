PKGBUILD: PKGBUILD_
	perl -pe 's/^\#\#.*$$//; s/^pkgver=xxx$$/pkgver=${version}/;' PKGBUILD_ > PKGBUILD
	makepkg -g >> PKGBUILD

archlinux: PKGBUILD
	echo todo
