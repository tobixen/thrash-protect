{
  lib,
  python3Packages,
  procps,
  src ? lib.cleanSource ../.,
  # hatch-vcs cannot see git metadata in a store path, so the version has to
  # be handed in; flake.nix derives one from the flake's revision.
  version ? "0+unknown",
}:

python3Packages.buildPythonApplication {
  pname = "thrash-protect";
  inherit version src;
  pyproject = true;

  env.SETUPTOOLS_SCM_PRETEND_VERSION = version;

  build-system = with python3Packages; [
    hatchling
    hatch-vcs
  ];

  # Only needed for YAML config files; TOML and JSON are covered by stdlib.
  dependencies = [ python3Packages.pyyaml ];

  # Process details in the log come from ps(1).
  makeWrapperArgs = [ "--prefix PATH : ${lib.makeBinPath [ procps ]}" ];

  nativeCheckInputs = [ python3Packages.pytestCheckHook ];

  pythonImportsCheck = [ "thrash_protect" ];

  meta = {
    description = "Suspends processes with SIGSTOP/SIGCONT to protect a host from thrashing";
    homepage = "https://github.com/tobixen/thrash-protect";
    license = lib.licenses.gpl3Plus;
    platforms = lib.platforms.linux;
    mainProgram = "thrash-protect";
  };
}
