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

  ## Monitoring
  if defined('nagios::nrpe') {
    $nagios_plugin_dir = $nagios::nrpe::nagios_plugin_dir
    file { '/etc/nagios/nrpe.d/thrash-protect.cfg':
      owner   => 'nagios',
      group   => 'nagios',
      mode    => '0440',
      content => template('thrash_protect/nrpe.cfg.erb')
    }
  }
}
