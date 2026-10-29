import 'dart:async';
import 'dart:convert';
import 'package:flet/flet.dart';
import 'package:flutter/services.dart';
import 'package:flutter_timezone/flutter_timezone.dart';
import 'package:saf_stream/saf_stream.dart';
import 'package:saf_util/saf_util.dart';

class Extension extends FletExtension {
  @override
  FletService? createService(Control control) =>
      control.type == 'FinanceSaf' ? FinanceSafService(control: control) : null;
}

class FinanceSafService extends FletService {
  FinanceSafService({required super.control});
  final _util = SafUtil();
  final _stream = SafStream();
  static Future<void> _tail = Future<void>.value();

  @override
  void init() {
    super.init();
    control.addInvokeMethodListener(_invoke);
  }

  @override
  void dispose() {
    control.removeInvokeMethodListener(_invoke);
    super.dispose();
  }

  Future<dynamic> _invoke(String method, dynamic args) async {
    // A Python timeout/cancellation must not let recovery race still-running I/O.
    final previous = _tail;
    final done = Completer<void>();
    _tail = done.future;
    await previous;
    try {
      return await _dispatch(method, args);
    } finally {
      done.complete();
    }
  }

  Future<dynamic> _dispatch(String method, dynamic args) async {
    try {
      return {'ok': true, 'value': await _execute(method, args)};
    } on PlatformException catch (error) {
      // Provider messages can contain document URIs. Return only a stable code.
      return {
        'ok': false,
        'error':
            'Android provider error (${error.code}). '
            'Check folder access, free space, and reselect the folder.',
      };
    } on FormatException {
      return {'ok': false, 'error': 'Invalid data received by SAF bridge.'};
    } on StateError catch (error) {
      return {'ok': false, 'error': error.message};
    }
  }

  Future<String?> _readDocument(String directory, String name) async {
    final matches = (await _util.list(
      directory,
    )).where((file) => file.name == name).toList();
    if (matches.isEmpty) return null;
    if (matches.length != 1 || matches.single.isDir) {
      throw StateError('Ambiguous storage document. Saving is blocked.');
    }
    return base64Encode(await _stream.readFileBytes(matches.single.uri));
  }

  Future<void> _record(String uri, String name, String encoded) async {
    final bytes = base64Decode(encoded);
    final existing = await _util.child(uri, [name]);
    if (existing == null) {
      final saved = await _stream.writeFileBytes(
        uri,
        name,
        'application/json',
        bytes,
        overwrite: false,
        append: false,
      );
      if (saved.fileName != name) {
        throw StateError(
          'Provider changed the recovery filename. Saving is blocked.',
        );
      }
    } else {
      if (existing.isDir) throw StateError('Recovery record is a directory.');
      await _stream.writeFileUriBytes(existing.uri, bytes, append: false);
    }
    if (await _readDocument(uri, name) != encoded) {
      throw StateError(
        'Recovery record verification failed. Saving is blocked.',
      );
    }
  }

  Future<Map<String, dynamic>> _snapshot(
    String uri,
    String target,
    String prefix,
    RegExp pendingName,
  ) async {
    final marker = await _readDocument(uri, '.$target.recovery.json');
    final directory = await _util.child(uri, ['backups']);
    final pending = <String>[];
    String? backup;
    if (directory != null) {
      if (!directory.isDir) throw StateError('Backups is not a directory.');
      pending.addAll(
        (await _util.list(directory.uri))
            .where((file) => !file.isDir && pendingName.hasMatch(file.name))
            .map((file) => file.name),
      );
      pending.sort();
      if (marker != null) {
        dynamic record;
        try {
          record = jsonDecode(utf8.decode(base64Decode(marker)));
        } on FormatException {
          // Return the damaged bytes for conservative Python recovery handling.
          record = null;
        }
        final path = record is Map ? record['backup'] : null;
        final valid = RegExp(
          '^backups/$prefix-[0-9]{8}T[0-9]{12}Z-[a-f0-9]{32}-(pending|committed)\\.beancount\$',
        );
        if (path is String && valid.hasMatch(path)) {
          final name = path.substring('backups/'.length);
          backup = await _readDocument(directory.uri, name);
          if (backup == null && name.contains('-pending.')) {
            backup = await _readDocument(
              directory.uri,
              name.replaceFirst('-pending.', '-committed.'),
            );
          }
        }
      }
    }
    return {
      'current': await _readDocument(uri, target),
      'marker': marker,
      'pending': pending,
      'backup': backup,
    };
  }

  Future<dynamic> _execute(String method, dynamic args) async {
    if (method == 'timezone') {
      return (await FlutterTimezone.getLocalTimezone()).identifier;
    }
    if (method == 'select') {
      final folder = await _util.pickDirectory(
        writePermission: true,
        persistablePermission: true,
      );
      return folder?.uri;
    }
    final uri = args['uri'] as String;
    final permitted = await _util.hasPersistedPermission(
      uri,
      checkRead: true,
      checkWrite: true,
    );
    if (method == 'status') return permitted;
    if (!permitted) {
      throw StateError(
        'Persistent read/write grant missing. Select Folder again.',
      );
    }
    final target = (args['filename'] as String?) ?? 'storage-test.beancount';
    if (target != 'storage-test.beancount' && target != 'textmoney.beancount') {
      throw StateError('Unsupported journal filename.');
    }
    final prefix = target == 'storage-test.beancount'
        ? 'storage-test'
        : 'textmoney';
    if (target == 'textmoney.beancount') {
      if (await _util.child(uri, ['.textmoney-migration-incomplete']) != null) {
        throw StateError('Incomplete migration. Review the migration output before opening.');
      }
    }
    final pendingName = RegExp(
      '^$prefix-[0-9]{8}T[0-9]{12}Z-[a-f0-9]{32}-pending\\.beancount\$',
    );
    final committedName = RegExp(
      '^$prefix-[0-9]{8}T[0-9]{12}Z-[a-f0-9]{32}-committed\\.beancount\$',
    );
    if (method == 'list_siblings') {
      final names =
          (await _util.list(uri))
              .where(
                (file) =>
                    !file.isDir &&
                    file.name != target &&
                    file.name.toLowerCase().endsWith('.beancount'),
              )
              .map((file) => file.name)
              .toList()
            ..sort();
      return names;
    }
    if (method == 'recovery') {
      return _snapshot(uri, target, prefix, pendingName);
    }
    if (method == 'finish_write' || method == 'accept_recovery') {
      final snapshot = await _snapshot(uri, target, prefix, pendingName);
      final encoded = args['record'] as String;
      final record =
          jsonDecode(utf8.decode(base64Decode(encoded)))
              as Map<String, dynamic>;
      if (method == 'finish_write') {
        if (snapshot['marker'] != encoded ||
            snapshot['current'] != args['data']) {
          throw StateError(
            'Journal or recovery record changed. Save outcome is uncertain.',
          );
        }
        final completed =
            jsonDecode(utf8.decode(base64Decode(args['completed'] as String)))
                as Map<String, dynamic>;
        if (completed['phase'] != 'complete' ||
            jsonEncode(completed['pending']) !=
                jsonEncode(snapshot['pending'])) {
          throw StateError(
            'Pending recovery copies changed. Saving is blocked.',
          );
        }
      } else {
        if (snapshot['marker'] != args['marker'] ||
            snapshot['current'] != args['current']) {
          throw StateError(
            'Journal changed during recovery. Reload and review again.',
          );
        }
        final backup = record['backup'] as String;
        final name = backup.startsWith('backups/') ? backup.substring(8) : '';
        if (!pendingName.hasMatch(name) || record['phase'] != 'resolved') {
          throw StateError('Invalid recovery confirmation.');
        }
        final directory = await _util.child(uri, ['backups']);
        if (directory == null ||
            await _readDocument(directory.uri, name) != args['current']) {
          throw StateError('Recovery snapshot backup could not be verified.');
        }
        final pending = List<String>.from(snapshot['pending'] as List)
          ..remove(name);
        if (jsonEncode(pending) != jsonEncode(args['pending'])) {
          throw StateError(
            'Recovery backups changed. Reload and review again.',
          );
        }
        if (jsonEncode(record['pending']) != jsonEncode(snapshot['pending'])) {
          throw StateError(
            'Recovery acknowledgment does not match pending copies.',
          );
        }
        if (snapshot['marker'] != null) {
          final archive = args['archive'] as String;
          if (!RegExp(
                '^$prefix-[0-9]{8}T[0-9]{12}Z-[a-f0-9]{32}-pending-recovery\\.json\$',
              ).hasMatch(archive) ||
              await _util.child(directory.uri, [archive]) != null) {
            throw StateError('Invalid recovery archive name.');
          }
          await _record(directory.uri, archive, snapshot['marker'] as String);
        }
        if (await _readDocument(uri, target) != args['current'] ||
            await _readDocument(uri, '.$target.recovery.json') !=
                args['marker']) {
          throw StateError(
            'Journal changed during recovery. No journal was overwritten.',
          );
        }
      }
      await _record(
        uri,
        '.$target.recovery.json',
        method == 'finish_write' ? args['completed'] as String : encoded,
      );
      return null;
    }
    if (method == 'read') {
      final file = await _util.child(uri, [target]);
      if (file == null) return null;
      if (file.isDir) throw StateError('Selected journal is a directory.');
      return base64Encode(await _stream.readFileBytes(file.uri));
    }
    if (method == 'backup') {
      final name = args['name'] as String;
      if (!pendingName.hasMatch(name)) {
        throw StateError('Invalid backup name.');
      }
      final directory = await _util.mkdirp(uri, ['backups']);
      if (await _util.child(directory.uri, [name]) != null) {
        throw StateError('Backup name already exists; retry.');
      }
      final bytes = base64Decode(args['data'] as String);
      final saved = await _stream.writeFileBytes(
        directory.uri,
        name,
        'application/octet-stream',
        bytes,
        overwrite: false,
        append: false,
      );
      if (saved.fileName != name ||
          base64Encode(await _stream.readFileBytes(saved.uri.toString())) !=
              base64Encode(bytes)) {
        throw StateError(
          'Backup verification failed; original was not written.',
        );
      }
      return 'backups/$name';
    }
    if (method == 'commit_backup' ||
        method == 'list_backups' ||
        method == 'delete_backup') {
      final directory = await _util.child(uri, ['backups']);
      if (directory == null) {
        if (method == 'list_backups') return <Map<String, dynamic>>[];
        throw StateError('Backups folder disappeared. Review recovery copies.');
      }
      if (!directory.isDir) throw StateError('Backups is not a directory.');
      if (method == 'list_backups') {
        final files = await _util.list(directory.uri);
        return files
            .where((file) => !file.isDir && committedName.hasMatch(file.name))
            .map((file) => {'name': file.name, 'size': file.length})
            .toList();
      }
      final name = args['name'] as String;
      final valid = method == 'commit_backup'
          ? pendingName.hasMatch(name)
          : committedName.hasMatch(name);
      if (!valid) throw StateError('Unrecognized backup name.');
      final file = await _util.child(directory.uri, [name]);
      if (file == null || file.isDir) {
        throw StateError('Backup changed during cleanup. Review and retry.');
      }
      if (method == 'commit_backup') {
        final newName = name.replaceFirst('-pending.', '-committed.');
        if (await _util.child(directory.uri, [newName]) != null) {
          throw StateError(
            'Finalized backup already exists. Recovery copy retained.',
          );
        }
        final renamed = await _util.rename(file.uri, false, newName);
        if (renamed.name != newName) {
          throw StateError(
            'Provider changed the backup filename. Inspect recovery copies.',
          );
        }
        return 'backups/$newName';
      }
      if (file.length < 0 || file.length != args['size']) {
        throw StateError(
          'Backup size changed during cleanup. Review and retry.',
        );
      }
      await _util.delete(file.uri, false);
      if (await _util.child(directory.uri, [name]) != null) {
        throw StateError(
          'Provider did not remove the old backup. Review and retry.',
        );
      }
      return true;
    }
    if (method == 'write') {
      final snapshot = await _snapshot(uri, target, prefix, pendingName);
      if (snapshot['marker'] != args['marker'] ||
          snapshot['current'] != args['previous']) {
        throw StateError('Journal changed before saving. Reload and review.');
      }
      final encoded = args['record'] as String;
      final record =
          jsonDecode(utf8.decode(base64Decode(encoded)))
              as Map<String, dynamic>;
      if (record['phase'] != 'prepared' || record['version'] != 1) {
        throw StateError('Invalid prepared save record.');
      }
      if (args['previous'] != null) {
        final backup = record['backup'];
        final name = backup is String && backup.startsWith('backups/')
            ? backup.substring(8)
            : '';
        final directory = await _util.child(uri, ['backups']);
        if (!pendingName.hasMatch(name) ||
            directory == null ||
            await _readDocument(directory.uri, name) != args['previous']) {
          throw StateError('Pre-write recovery backup is missing or corrupt.');
        }
      }
      await _record(uri, '.$target.recovery.json', encoded);
      if (await _readDocument(uri, target) != args['previous']) {
        throw StateError(
          'Journal changed after recovery preparation. Saving is blocked.',
        );
      }
      final bytes = base64Decode(args['data'] as String);
      final file = await _util.child(uri, [target]);
      if (file != null) {
        if (file.isDir) throw StateError('Selected journal is a directory.');
        await _stream.writeFileUriBytes(file.uri, bytes, append: false);
      } else {
        final saved = await _stream.writeFileBytes(
          uri,
          target,
          'application/octet-stream',
          bytes,
          overwrite: false,
          append: false,
        );
        if (saved.fileName != target) {
          throw StateError(
            'Provider changed the filename. Inspect the test folder.',
          );
        }
      }
      if (await _readDocument(uri, target) != args['data']) {
        throw StateError(
          'Journal read-back differs. Save outcome is uncertain.',
        );
      }
      return null;
    }
    throw StateError('Unsupported SAF operation.');
  }
}
