import io
import os

CRYPT4GH_DEFAULT_EXT = 'crypt4gh'
CRYPT4GH_MAGIC_NUMBER_AND_VERSION = b'crypt4gh' + int.to_bytes(1, length=4, byteorder='little')


def is_valid_crypt4gh_file(file_path: str) -> bool:
    try:
        with open(file_path, 'rb') as file_stream:
            header = read_and_validate_crypt4gh_header(file_stream)
            return file_has_encrypted_data(header, file_size=os.path.getsize(file_path))
    except Exception:
        return False


def file_has_encrypted_data(header: bytes, file_size: int):
    return len(header) < file_size

def read_and_validate_crypt4gh_header(stream: io.BytesIO) -> bytes:
    header = b""

    prefix_bytes = stream.read(16)
    if prefix_bytes[0:len(CRYPT4GH_MAGIC_NUMBER_AND_VERSION)] != CRYPT4GH_MAGIC_NUMBER_AND_VERSION:
        raise ValueError("Unable to read Crypt4GH header. Not a Crypt4GH dataset")
    header += prefix_bytes

    header_packet_count = int.from_bytes(prefix_bytes[12:16], byteorder="little")
    for i in range(header_packet_count):
        packet_length = int.from_bytes(stream.read(4), byteorder="little")
        stream.seek(-4, os.SEEK_CUR)
        packet_bytes = stream.read(packet_length)
        header += packet_bytes

    return header
