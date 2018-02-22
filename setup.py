from distutils.core import setup

try:
    # noinspection PyPackageRequirements
    from pypandoc import convert
    read_md = lambda f: convert(f, 'rst')
except ImportError:
    print("warning: pypandoc not found, could not convert Markdown to RST")
    read_md = lambda f: open(f, 'r').read()

setup(
    name='django_sphinxsearch',
    version='2.1.0',
    long_description=read_md('README.md'),
    packages=[
        'sphinxsearch',
        'sphinxsearch.backend',
        'sphinxsearch.backend.sphinx',
    ],
    url='http://github.com/rutube/django_sphinxsearch',
    license='Beerware',
    author='tumbler',
    author_email='zimbler@gmail.com',
    description='Sphinxsearch database backend for django>=2.0',
    setup_requires=[
        'Django>=2.0,<2.1',
        'mysqlclient>=1.3.3,<1.4.0'
    ],
)
