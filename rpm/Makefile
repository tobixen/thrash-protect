.PHONY: rpm update-version

update-version:
	perl -pe 's/^Version:.*/Version:        ${version}/' -i.bak thrash-protect.spec
	perl -pe 's/^Version:.*/Version:        ${version}/' -i.bak thrash-protect.rhel6.spec
	perl -pe 's/^Version:.*/Version:        ${version}/' -i.bak thrash-protect.rhel7.spec

rpm: update-version ../.tag.${version}
	rpmbuild -ba thrash-protect.spec
