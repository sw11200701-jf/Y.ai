"""Read one original Office file on stdin and emit a normalized package."""
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time

import uno
from com.sun.star.beans import PropertyValue

MAX_BYTES = 40 * 1024 * 1024
TARGETS = {
    "xls": "xlsx", "xlsb": "xlsx", "ods": "xlsx",
    "ppt": "pptx", "odp": "pptx",
    "rtf": "docx", "odt": "docx",
}
FILTERS = {
    "docx": ("com.sun.star.text.TextDocument", "Office Open XML Text"),
    "xlsx": ("com.sun.star.sheet.SpreadsheetDocument", "Calc MS Excel 2007 XML"),
    "pptx": ("com.sun.star.presentation.PresentationDocument", "Impress MS PowerPoint 2007 XML"),
}


def properties(**values):
    result = []
    for name, value in values.items():
        item = PropertyValue()
        item.Name = name
        item.Value = value
        result.append(item)
    return tuple(result)


def connect(process, pipe):
    local = uno.getComponentContext()
    resolver = local.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", local)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and process.poll() is None:
        try:
            context = resolver.resolve(f"uno:pipe,name={pipe};urp;StarOffice.ComponentContext")
        except Exception:
            time.sleep(.1)
        else:
            return context.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", context)
    raise RuntimeError("OFFICE_WORKER_START_FAILED")


def convert(raw, kind):
    if kind not in TARGETS or not 0 < len(raw) <= MAX_BYTES:
        raise ValueError("OFFICE_INPUT_INVALID")
    output_kind = TARGETS[kind]
    service, output_filter = FILTERS[output_kind]
    with tempfile.TemporaryDirectory(prefix="office-source-") as folder:
        root = Path(folder)
        source = root / f"input.{kind}"
        target = root / f"output.{output_kind}"
        source.write_bytes(raw)
        profile = root / "profile"
        user = profile / "user"
        user.mkdir(parents=True)
        (user / "registrymodifications.xcu").write_text(
            '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
            '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
            '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>'
            '</item></oor:items>', encoding="utf-8"
        )
        pipe = "office_" + secrets.token_hex(12)
        process = subprocess.Popen(
            ["/usr/bin/libreoffice", "-env:UserInstallation=" + profile.as_uri(),
             "--headless", "--nologo", "--nodefault", "--norestore",
             f"--accept=pipe,name={pipe};urp;StarOffice.ComponentContext"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={**os.environ, "HOME": str(root), "SAL_USE_VCLPLUGIN": "svp"},
        )
        desktop = document = None
        try:
            desktop = connect(process, pipe)
            document = desktop.loadComponentFromURL(source.as_uri(), "_blank", 0, properties(
                Hidden=True, ReadOnly=True, Silent=True,
                MacroExecutionMode=uno.getConstantByName("com.sun.star.document.MacroExecMode.NEVER_EXECUTE"),
                UpdateDocMode=uno.getConstantByName("com.sun.star.document.UpdateDocMode.NO_UPDATE"),
            ))
            if document is None or not document.supportsService(service):
                raise ValueError("OFFICE_SOURCE_FORMAT_MISMATCH")
            if output_kind == "xlsx":
                document.enableAutomaticCalculation(False)
            document.storeToURL(target.as_uri(), properties(FilterName=output_filter, Overwrite=False))
            if not 0 < target.stat().st_size <= MAX_BYTES:
                raise ValueError("OFFICE_OUTPUT_SIZE_INVALID")
            return target.read_bytes()
        finally:
            if document is not None:
                try:
                    document.close(True)
                except Exception:
                    pass
            if desktop is not None:
                try:
                    desktop.terminate()
                except Exception:
                    pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    os.umask(0o077)
    try:
        kind, = sys.argv[1:]
        result = convert(sys.stdin.buffer.read(MAX_BYTES + 1), kind)
        sys.stdout.buffer.write(result)
    except Exception:
        sys.stderr.write("OFFICE_CONVERSION_FAILED\n")
        sys.exit(2)
