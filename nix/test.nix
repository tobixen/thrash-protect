# NixOS VM test: the service comes up with a generated config and survives.
{ module, package }:
{
  name = "thrash-protect";

  nodes.machine = {
    imports = [ module ];
    services.thrash-protect = {
      enable = true;
      inherit package;
      settings.interval = 0.25;
    };
    swapDevices = [
      {
        device = "/var/swapfile";
        size = 64;
      }
    ];
  };

  testScript = ''
    machine.wait_for_unit("thrash-protect.service")
    machine.succeed("systemctl cat thrash-protect.service | grep -- '--config'")
    machine.sleep(5)
    machine.succeed("systemctl is-active thrash-protect.service")
    machine.succeed("test \"$(systemctl show -P NRestarts thrash-protect.service)\" = 0")
    machine.succeed("thrash-protect --version")
  '';
}
