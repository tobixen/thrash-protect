thrash-protect.spec: ../thrash-protect.py
	perl -pe 's/^Version:.*/Version:        ${version}/' -i.bak thrash-protect.spec 
	perl -pe 's/^Version:.*/Version:        ${version}/' -i.bak thrash-protect.rhel6.spec 

rpm: thrash-protect.spec ../.tag.${version}
	rpmbuild -ba thrash-protect.spec
