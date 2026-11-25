package com.pioneer.notification_bridge;

import android.app.Notification;
import android.app.NotificationManager;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.service.notification.StatusBarNotification;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

import io.flutter.embedding.engine.plugins.FlutterPlugin;
import io.flutter.plugin.common.MethodCall;
import io.flutter.plugin.common.MethodChannel;

public final class NotificationBridgePlugin
        implements FlutterPlugin, MethodChannel.MethodCallHandler {
    private static final String CHANNEL = "com.pioneer.textmoney/notifications";
    private Context context;
    private MethodChannel channel;

    @Override
    public void onAttachedToEngine(FlutterPluginBinding binding) {
        context = binding.getApplicationContext();
        channel = new MethodChannel(binding.getBinaryMessenger(), CHANNEL);
        channel.setMethodCallHandler(this);
    }

    @Override
    public void onDetachedFromEngine(FlutterPluginBinding binding) {
        channel.setMethodCallHandler(null);
        channel = null;
        context = null;
    }

    private boolean hasAccess() {
        ComponentName component = new ComponentName(context, NotificationReader.class);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            NotificationManager manager = (NotificationManager)
                    context.getSystemService(Context.NOTIFICATION_SERVICE);
            return manager.isNotificationListenerAccessGranted(component);
        }
        String enabled = Settings.Secure.getString(
                context.getContentResolver(), "enabled_notification_listeners");
        if (enabled == null) return false;
        for (String value : enabled.split(":")) {
            if (component.equals(ComponentName.unflattenFromString(value))) return true;
        }
        return false;
    }

    private boolean listenerEnabled() {
        return context.getPackageManager().getComponentEnabledSetting(
                new ComponentName(context, NotificationReader.class))
                == PackageManager.COMPONENT_ENABLED_STATE_ENABLED;
    }

    @Override
    public void onMethodCall(MethodCall call, MethodChannel.Result result) {
        switch (call.method) {
            case "status":
                Map<String, Object> status = new HashMap<>();
                status.put("access", hasAccess());
                status.put("connected", hasAccess() && listenerEnabled()
                        && NotificationReader.connected() != null);
                result.success(status);
                break;
            case "open_settings":
                try {
                    Intent settings = new Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS);
                    settings.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                    context.startActivity(settings);
                    result.success(null);
                } catch (android.content.ActivityNotFoundException | SecurityException error) {
                    result.error("settings_unavailable", "Notification access settings unavailable.", null);
                }
                break;
            case "enable":
            case "disable":
                try {
                    context.getPackageManager().setComponentEnabledSetting(
                            new ComponentName(context, NotificationReader.class),
                            call.method.equals("enable")
                                    ? PackageManager.COMPONENT_ENABLED_STATE_ENABLED
                                    : PackageManager.COMPONENT_ENABLED_STATE_DISABLED,
                            PackageManager.DONT_KILL_APP);
                    result.success(null);
                } catch (SecurityException error) {
                    result.error("listener_unavailable", "Notification listener is unavailable.", null);
                }
                break;
            case "apps":
                result.success(apps());
                break;
            case "storage_dir":
                result.success(context.getNoBackupFilesDir().getAbsolutePath());
                break;
            case "active":
                active(call, result);
                break;
            default:
                result.notImplemented();
        }
    }

    private List<Map<String, String>> apps() {
        PackageManager manager = context.getPackageManager();
        Intent launcher = new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER);
        List<ResolveInfo> activities = manager.queryIntentActivities(launcher, 0);
        Set<String> seen = new HashSet<>();
        List<Map<String, String>> apps = new ArrayList<>();
        for (ResolveInfo activity : activities) {
            String id = activity.activityInfo.packageName;
            if (id.equals(context.getPackageName()) || !seen.add(id)) continue;
            Map<String, String> app = new HashMap<>();
            app.put("id", id);
            app.put("name", activity.loadLabel(manager).toString());
            apps.add(app);
        }
        Collections.sort(apps, Comparator.comparing(app -> app.get("name")));
        return apps;
    }

    private void active(MethodCall call, MethodChannel.Result result) {
        if (!hasAccess()) {
            result.error("access_required", "Notification access is required.", null);
            return;
        }
        NotificationReader reader = NotificationReader.connected();
        if (!listenerEnabled() || reader == null) {
            result.error("listener_unavailable", "Notification listener is not connected.", null);
            return;
        }
        List<?> selected = call.argument("apps");
        if (selected == null || selected.isEmpty() || selected.size() > 200) {
            result.error("invalid_apps", "Choose at least one source app.", null);
            return;
        }
        Set<String> allowed = new HashSet<>();
        for (Object value : selected) {
            if (!(value instanceof String) || ((String) value).isEmpty()) {
                result.error("invalid_apps", "Invalid source app.", null);
                return;
            }
            allowed.add((String) value);
        }
        try {
            StatusBarNotification[] active = reader.getActiveNotifications();
            if (active == null) {
                result.error("listener_unavailable", "Notification listener is not connected.", null);
                return;
            }
            List<Map<String, Object>> notifications = new ArrayList<>();
            for (StatusBarNotification item : active) {
                if (!allowed.contains(item.getPackageName())) continue;
                Notification notification = item.getNotification();
                if ((notification.flags & Notification.FLAG_GROUP_SUMMARY) != 0) continue;
                Bundle extras = notification.extras;
                CharSequence rawTitle = extras.getCharSequence(Notification.EXTRA_TITLE);
                CharSequence rawBody = extras.getCharSequence(Notification.EXTRA_BIG_TEXT);
                if (rawBody == null) rawBody = extras.getCharSequence(Notification.EXTRA_TEXT);
                String title = rawTitle == null ? null : rawTitle.toString();
                String body = rawBody == null ? null : rawBody.toString();
                if ((title != null && title.length() > 4096)
                        || (body != null && body.length() > 4096)) {
                    result.error("content_too_large", "A notification exceeds the supported size.", null);
                    return;
                }
                Map<String, Object> value = new HashMap<>();
                value.put("app_id", item.getPackageName());
                value.put("key", version(item.getKey(), null));
                value.put("posted", item.getPostTime() / 1000);
                value.put("posted_millis", item.getPostTime());
                value.put("version", version(title, body));
                value.put("title", title);
                value.put("body", body);
                notifications.add(value);
            }
            result.success(notifications);
        } catch (SecurityException error) {
            result.error("access_required", "Notification access is required.", null);
        } catch (NoSuchAlgorithmException error) {
            result.error("digest_unavailable", "Notification identity is unavailable.", null);
        }
    }

    private static String version(String title, String body) throws NoSuchAlgorithmException {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        for (String field : new String[]{title, body}) {
            if (field == null) {
                digest.update((byte) 0);
            } else {
                byte[] bytes = field.getBytes(StandardCharsets.UTF_8);
                digest.update((byte) 1);
                digest.update(new byte[]{
                        (byte) (bytes.length >>> 24), (byte) (bytes.length >>> 16),
                        (byte) (bytes.length >>> 8), (byte) bytes.length
                });
                digest.update(bytes);
            }
        }
        StringBuilder encoded = new StringBuilder();
        for (byte value : digest.digest()) encoded.append(String.format("%02x", value & 0xff));
        return encoded.toString();
    }
}
