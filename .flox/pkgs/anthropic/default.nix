# Anthropic Python SDK 1.3.0.
#
# The catalog carries 0.75.0, a major version behind. The 1.x line is what this
# service is built on: client.beta.messages.tool_runner, @beta_tool(strict=True),
# output_config effort, and the fallbacks parameter all live there. 1.x also moved
# off httpx onto httpx2, which is not in nixpkgs, so httpx2 and its pinned
# httpcore2 are built here and propagated — that also makes `import httpx2` work
# for callers, which app/intercom.py relies on.
#
# All three build from wheels rather than sdists. httpx2 and httpcore2 take their
# version from git via uv-dynamic-versioning (>=0.14.0, catalog has 0.12.0); an
# sdist has no git, so they would build as 0.0.0 and break httpx2's
# `httpcore2==2.12.0` pin. These are pure-Python packages, so the published wheel
# is the same artifact with correct metadata and no build backend needed.
{ python312Packages, fetchPypi, lib }:

let
  py = python312Packages;

  wheelSrc = pname: version: hash:
    fetchPypi {
      inherit pname version hash;
      format = "wheel";
      dist = "py3";
      python = "py3";
    };

  httpcore2 = py.buildPythonPackage rec {
    pname = "httpcore2";
    version = "2.12.0";
    format = "wheel";
    src = wheelSrc pname version "sha256-fgQljOAQE9fWFeW5EKOyf6yTfXqVA4In55ZStLo7TOs=";
    dependencies = [ py.h11 py.truststore py.anyio ];
    doCheck = false;
    pythonImportsCheck = [ "httpcore2" ];
  };

  httpx2 = py.buildPythonPackage rec {
    pname = "httpx2";
    version = "2.12.0";
    format = "wheel";
    src = wheelSrc pname version "sha256-zItu7LhmHBRrj4mmDpdFbuCG6Rp4TtMaxFDDqeYT3TY=";
    dependencies = [ httpcore2 py.idna py.anyio py.truststore py.certifi ];
    # Catalog idna is 3.11; upstream asks for >=3.18. The bound tracks newer IDNA
    # test vectors, not an API change this client path exercises.
    pythonRelaxDeps = [ "idna" ];
    doCheck = false;
    pythonImportsCheck = [ "httpx2" ];
  };
in
# Built fresh rather than overrideAttrs on the catalog's 0.75.0: that recipe sets
# `pyproject = true`, which wins over `format = "wheel"` and leaves the wheel
# unpack hook off. Dependencies below are anthropic 1.3.0's own requires_dist.
py.buildPythonPackage rec {
  pname = "anthropic";
  version = "1.3.0";
  format = "wheel";
  src = wheelSrc pname version "sha256-5+fb6/nzyEojlUq5iTeK9q4QpNGATIHp/qS1ztaVznU=";

  dependencies = [
    httpx2
    py.anyio
    py.docstring-parser
    py.jiter
    py.pydantic
    py.sniffio
    py.typing-extensions
  ];

  doCheck = false;
  pythonImportsCheck = [ "anthropic" ];

  meta = {
    description = "Anthropic Python SDK 1.3.0 (newer than the catalog's 0.75.0)";
    homepage = "https://github.com/anthropics/anthropic-sdk-python";
    license = lib.licenses.mit;
  };
}
