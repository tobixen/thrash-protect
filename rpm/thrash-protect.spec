Name:           thrash-protect
Version:        0.5.4.5
Release:        1%{?dist}
Summary:        Simple-Stupid user-space program protecting a linux host from thrashing
BuildArch:      noarch
Group:          System Environment/Daemons
License:        GPLv3
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
mkdir -p $RPM_BUILD_ROOT/lib/systemd/system
mkdir -p $RPM_BUILD_ROOT/sbin
mkdir -p $RPM_BUILD_ROOT%{_defaultdocdir}/%{name}-%{version}
make INSTALL_ROOT=$RPM_BUILD_ROOT


%files
/usr/sbin/thrash-protect
/usr/lib/systemd/system/thrash-protect.service

%doc README.md ChangeLog


%changelog
