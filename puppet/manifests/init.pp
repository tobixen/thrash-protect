class thrash_protect (
  $enable=hiera('thrash_protect::enable', true)
  ) {
  if ($enable) {
    package { 'thrash-protect':
      ensure => installed;
    }
    service { 'thrash-protect':
      enable => true,
      ensure => running;
    }
  } else {
    service { 'thrash-protect':
      enable => false,
      ensure => stopped;
    }
  }
}
