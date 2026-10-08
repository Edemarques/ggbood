set -x
mkdir -p ~/tgopt/harness/test
cd ~/tgopt/harness/test
rm -rf v1
python3 -m venv --without-pip --system-site-packages v1 && ls v1/lib/python3.14/site-packages
python3 -c 'import site; print(site.getusersitepackages())' > v1/lib/python3.14/site-packages/usersite.pth
cat v1/lib/python3.14/site-packages/usersite.pth
HOME=/tmp PYTHONPATH= v1/bin/python -c 'import sys, pytest; print(sys.executable, pytest.__file__); print(sys.path)'
HOME=/tmp v1/bin/python -m pytest --version
