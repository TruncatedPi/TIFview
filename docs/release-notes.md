Remove the obsolete startup warning that said Photoshop TIFF export still needed
Photoshop and RIP validation. The user has already validated the sample workflow
in Photoshop and PrintExp on the shop PC.

Download the [**TIFview 0.4.2 Windows x64 ZIP**](https://github.com/TruncatedPi/TIFview/releases/download/v0.4.2/TIFview-0.4.2-windows-x64.zip),
extract the entire archive, and run **TIFview/TIFview.exe**. Python is included.
Rerun `setup.cmd` from the source ZIP for automatic updating.

The empty viewer no longer shows the old blanket warning. File-specific reading,
channel interpretation and colour notes still appear after opening a file when
applicable. Channel/layer viewing, editing and TIFF-copy saving are unchanged.

The version remains visible in the title, bottom-right label and Help > About.
README line 1 and the EXE's Explorer File/Product version identify this release.
