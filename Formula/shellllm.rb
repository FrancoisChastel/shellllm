class Shellllm < Formula
  include Language::Python::Virtualenv

  desc "Local-LLM helpers for zsh: ',' proposes commands, '?' answers questions"
  homepage "https://github.com/FrancoisChastel/shellllm"
  url "https://github.com/FrancoisChastel/shellllm/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "REPLACE_AT_RELEASE_TARBALL_SHA256"
  license "MIT"
  head "https://github.com/FrancoisChastel/shellllm.git", branch: "main"

  depends_on "fzf"
  depends_on "llama.cpp"
  depends_on "python@3.12"

  # PyPI resources — regenerate with `scripts/brew/generate-resources.sh`.
  # Versions and hashes are filled in by that script before each release.

  resource "anyio" do
    url "https://files.pythonhosted.org/packages/source/a/anyio/anyio-4.6.2.tar.gz"
    sha256 "REPLACE_RESOURCE_SHA256"
  end

  resource "certifi" do
    url "https://files.pythonhosted.org/packages/source/c/certifi/certifi-2024.8.30.tar.gz"
    sha256 "REPLACE_RESOURCE_SHA256"
  end

  resource "h11" do
    url "https://files.pythonhosted.org/packages/source/h/h11/h11-0.14.0.tar.gz"
    sha256 "REPLACE_RESOURCE_SHA256"
  end

  resource "httpcore" do
    url "https://files.pythonhosted.org/packages/source/h/httpcore/httpcore-1.0.6.tar.gz"
    sha256 "REPLACE_RESOURCE_SHA256"
  end

  resource "httpx" do
    url "https://files.pythonhosted.org/packages/source/h/httpx/httpx-0.27.2.tar.gz"
    sha256 "REPLACE_RESOURCE_SHA256"
  end

  resource "idna" do
    url "https://files.pythonhosted.org/packages/source/i/idna/idna-3.10.tar.gz"
    sha256 "REPLACE_RESOURCE_SHA256"
  end

  resource "markdown-it-py" do
    url "https://files.pythonhosted.org/packages/source/m/markdown-it-py/markdown-it-py-3.0.0.tar.gz"
    sha256 "REPLACE_RESOURCE_SHA256"
  end

  resource "mdurl" do
    url "https://files.pythonhosted.org/packages/source/m/mdurl/mdurl-0.1.2.tar.gz"
    sha256 "REPLACE_RESOURCE_SHA256"
  end

  resource "pygments" do
    url "https://files.pythonhosted.org/packages/source/p/pygments/pygments-2.18.0.tar.gz"
    sha256 "REPLACE_RESOURCE_SHA256"
  end

  resource "rich" do
    url "https://files.pythonhosted.org/packages/source/r/rich/rich-13.9.4.tar.gz"
    sha256 "REPLACE_RESOURCE_SHA256"
  end

  resource "sniffio" do
    url "https://files.pythonhosted.org/packages/source/s/sniffio/sniffio-1.3.1.tar.gz"
    sha256 "REPLACE_RESOURCE_SHA256"
  end

  def install
    virtualenv_install_with_resources

    # Ship the zsh integration file at a stable, well-known location.
    pkgshare.install "zsh/shellllm.zsh"
    pkgshare.install "bash/shellllm.bash"
  end

  def caveats
    <<~EOS
      To wire the `,` `,,` `?` `??` `???` helpers into zsh, add this to ~/.zshrc:

        source "#{opt_pkgshare}/shellllm.zsh"

      Then reload your shell, grab a model, and start the backend:

        huggingface-cli download unsloth/Qwen3.6-27B-GGUF   # one-time
        ??              # default tier (balanced)
        ?? --list       # show tiers and what's downloaded
        ?? --start fast # MoE + MTP — fastest on Apple Silicon

      (Or `export SHELLLM_AUTOSTART=1` and skip `??` — the first `,` or `?`
      starts the server for you.)

      Downloading tier models requires `huggingface-cli`:

        pipx install huggingface_hub

      shellllm never auto-executes commands and never phones home.
    EOS
  end

  test do
    # Verify the venv installed every entry-point and every module imports.
    assert_predicate bin/"shellllm-comma", :executable?
    assert_predicate bin/"shellllm-ask", :executable?
    assert_predicate bin/"shellllm-recall", :executable?

    system libexec/"bin/python", "-c", <<~PY
      import shellllm
      import shellllm.ask
      import shellllm.client
      import shellllm.comma
      import shellllm.recall
      import shellllm.safe_fs
      import shellllm.shell_context
      import shellllm.web
      assert shellllm.__version__
    PY

    # Confirm the zsh integration shipped where caveats say it does.
    assert_predicate pkgshare/"shellllm.zsh", :exist?
    assert_predicate pkgshare/"shellllm.bash", :exist?
  end
end
