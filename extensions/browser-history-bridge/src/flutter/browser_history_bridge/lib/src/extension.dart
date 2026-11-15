import 'package:flet/flet.dart';

import 'history_stub.dart' if (dart.library.js_interop) 'history_web.dart' as history;

class Extension extends FletExtension {
  @override
  FletService? createService(Control control) =>
      control.type == 'BrowserHistory'
      ? BrowserHistoryService(control: control)
      : null;
}

class BrowserHistoryService extends FletService {
  BrowserHistoryService({required super.control});

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
    if (method != 'back') {
      throw UnsupportedError('Unknown browser history method.');
    }
    history.back();
    return null;
  }
}
