{
  config,
  lib,
  pkgs,
  ...
}:

let
  cfg = config.services.thrash-protect;
  format = pkgs.formats.yaml { };
  configFile = format.generate "thrash-protect.yaml" { thrash-protect = cfg.settings; };
in
{
  options.services.thrash-protect = {
    enable = lib.mkEnableOption "thrash-protect, a daemon suspending processes to stop the host from thrashing";

    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.callPackage ./package.nix { };
      defaultText = lib.literalExpression "pkgs.callPackage ./package.nix { }";
      description = "The thrash-protect package to use.";
    };

    settings = lib.mkOption {
      type = format.type;
      default = { };
      example = {
        interval = 0.5;
        cmd_whitelist = [
          "sshd"
          "bash"
        ];
      };
      description = ''
        Settings for the `thrash-protect:` section of the config file; see
        thrash-protect.yaml.example for the keys.  Left empty, the built-in
        defaults apply.
      '';
    };

    extraArgs = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      example = [ "--diagnostic" ];
      description = "Extra command line arguments; they override `settings`.";
    };
  };

  config = lib.mkIf cfg.enable {
    environment.systemPackages = [ cfg.package ];

    # Mirrors systemd/thrash-protect.service; the reasons for each setting are
    # given there.
    systemd.services.thrash-protect = {
      description = "Thrash protecting daemon";
      wantedBy = [ "multi-user.target" ];
      startLimitBurst = 5;
      startLimitIntervalSec = 60;
      serviceConfig = {
        ExecStart = lib.escapeShellArgs (
          [ (lib.getExe cfg.package) ]
          ++ lib.optionals (cfg.settings != { }) [
            "--config"
            "${configFile}"
          ]
          ++ cfg.extraArgs
        );
        KillMode = "process";
        Restart = "always";
        RestartSec = 5;
        Nice = -15;
        OOMScoreAdjust = -900;
      };
    };
  };
}
