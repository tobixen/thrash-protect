PKGBUILD: PKGBUILD_ ../thrash-protect.py ../ChangeLog
	perl -pe 's/^\#\#.*$$//; s/^pkgver=xxx$$/pkgver=${version}/;' PKGBUILD_ > PKGBUILD
	makepkg -g >> PKGBUILD

"${pkgname}-${version}-1.src.tar.gz": PKGBUILD
	makepkg --source

archlinux: "${pkgname}-${version}-1.src.tar.gz"
	burp -C${HOME}/.config/burp.cookies -k "${pkgname}-${version}-1.src.tar.gz"
