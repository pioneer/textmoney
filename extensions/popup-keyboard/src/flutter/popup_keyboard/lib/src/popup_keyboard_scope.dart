import 'package:flutter/widgets.dart';

class PopupKeyboardScope extends StatelessWidget {
  const PopupKeyboardScope({
    super.key,
    required this.onEscape,
    required this.child,
  });

  final VoidCallback onEscape;
  final Widget child;

  @override
  Widget build(BuildContext context) => Actions(
    dispatcher: _PopupActionDispatcher(onEscape),
    actions: const {},
    child: child,
  );
}

class _PopupActionDispatcher extends ActionDispatcher {
  const _PopupActionDispatcher(this.onEscape);

  final VoidCallback onEscape;

  @override
  Object? invokeAction(
    covariant Action<Intent> action,
    covariant Intent intent, [
    BuildContext? context,
  ]) {
    if (_recoverEscape(action, intent)) return null;
    return super.invokeAction(action, intent, context);
  }

  @override
  (bool, Object?) invokeActionIfEnabled(
    covariant Action<Intent> action,
    covariant Intent intent, [
    BuildContext? context,
  ]) {
    if (_recoverEscape(action, intent)) return (true, null);
    return super.invokeActionIfEnabled(action, intent, context);
  }

  bool _recoverEscape(Action<Intent> action, Intent intent) {
    // DropdownMenu retains its internal Escape shortcut after its menu closes.
    // Keep open-menu dismissal native; recover only the otherwise lost key.
    if (intent is DismissIntent &&
        action is DismissMenuAction &&
        action.isEnabled(intent) &&
        !action.controller.isOpen) {
      onEscape();
      return true;
    }
    return false;
  }
}
