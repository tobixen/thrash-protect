Name:           thrash-protect
Version:        0.5.3
Release:        1%{?dist}
Summary:        Simple-Stupid user-space program protecting a linux host from thrashing.
BuildArch:      noarch
Group:          System Environment/Daemons
License:        GPL3
URL:            https://github.com/tobixen/thrash-protect  
Source0:        https://github.com/tobixen/%{name}/archive/v%{version}.tar.gz
Requires:       python

%description
The program will on fixed intervals check if there has been a lot of
swapping since previous run, and if there are a lot of swapping, the
program with the most page faults will be temporary suspended.  This
way the host will never become so thrashed up that it won't be
possible for a system administrator to ssh into the box and fix the
problems, and in many cases the problems will resolve by themselves.

%prep
%setup -q

%build
true

%install
make install prefix=$RPM_BUILD_ROOT


%doc README.md ChangeLog


%changelog
