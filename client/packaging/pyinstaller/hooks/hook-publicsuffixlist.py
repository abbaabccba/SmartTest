from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files("publicsuffixlist", includes=["public_suffix_list.dat"])
