"""`ccx-install`: the pipx/uv entry point. Runs install-resume.py from the files bundled in this package."""
import importlib.util
from pathlib import Path


def main():
    here = Path(__file__).resolve().parent
    # In a wheel the checkout's files sit in ccx_files/; from a checkout they sit beside this file.
    root = here / 'ccx_files' if (here / 'ccx_files').is_dir() else here
    spec = importlib.util.spec_from_file_location('ccx_install_resume', root / 'install-resume.py')
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    installer.main()


if __name__ == '__main__':
    main()
